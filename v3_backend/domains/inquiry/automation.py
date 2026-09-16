"""Preflight for unattended inquiry generation. Source quantities never change."""

import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm.attributes import flag_modified

from domains.inquiry.models import SupplierTemplate
from domains.inquiry.template_contract import normalized_contract
from domains.inquiry.template_selector import template_has_zone_config
from domains.masterdata import Product, Supplier


def positive(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            raise ValueError()
        return number
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("QUANTITY_INVALID") from None


def prepare_inquiry(db, order, source, *, unit_approvals=None, template_overrides=None):
    issues, prepared, bindings = [], [], {}
    approvals, overrides = unit_approvals or [], template_overrides or {}
    results = order.match_results or []
    if not results or len(results) != len(order.products or []):
        return [{"code": "MATCH_LINE_COVERAGE"}], {}
    for original, result in zip(order.products, results, strict=True):
        line_id = original.get("line_id")

        def reject(code, line_id=line_id, target=result):
            issues.append({"code": code, "line_id": line_id})
            target["inquiry_exclusion_code"] = code
            target["inquiry_exclusion_reason"] = {
                "EXACT_UNIQUE_MATCH_REQUIRED": "未找到唯一的精确商品匹配",
                "SUPPLIER_REQUIRED": "匹配商品未配置供应商",
                "QUANTITY_INVALID": "数量必须是大于 0 的数字",
                "UNIT_REQUIRED": "订购单位或供应商单位缺失",
                "UNIT_CONVERSION_EVIDENCE_REQUIRED": "订购单位不同，需要人工确认换算关系",
                "UNIT_CONVERSION_INCONSISTENT": "已提供的单位换算关系不一致",
                "TEMPLATE_BINDING_REQUIRED": "供应商询价模板无法唯一确定",
                "TEMPLATE_FIELDS_REQUIRED": "供应商模板缺少询价必填字段",
            }.get(code, code)

        if (
            result.get("line_id") != line_id
            or result.get("match_status") != "matched"
            or result.get("match_score") != 1.0
        ):
            reject("EXACT_UNIQUE_MATCH_REQUIRED")
            continue
        matched = result.get("matched_product") or {}
        product = db.get(Product, matched.get("id")) if matched.get("id") else None
        if (
            product is None
            or not product.supplier_id
            or db.get(Supplier, product.supplier_id) is None
        ):
            reject("SUPPLIER_REQUIRED")
            continue
        try:
            quantity = positive(original.get("quantity"))
        except ValueError:
            reject("QUANTITY_INVALID")
            continue
        raw_unit = str(original.get("unit") or "").strip()
        target_unit = str(product.unit or "").strip()
        approved = None
        if not raw_unit or not target_unit:
            reject("UNIT_REQUIRED")
            continue
        if raw_unit.upper() != target_unit.upper():
            candidates = [
                a
                for a in approvals
                if all(
                    a.get(k) == v
                    for k, v in {
                        "source_key": source.source_key,
                        "version_key": source.version_key,
                        "pdf_sha256": source.pdf_sha256,
                        "line_id": line_id,
                        "product_id": product.id,
                        "supplier_id": product.supplier_id,
                        "port_id": order.port_id,
                        "delivery_date": order.delivery_date,
                        "source_unit": raw_unit,
                        "supplier_unit": target_unit,
                        "pack_size": product.pack_size,
                    }.items()
                )
                and a.get("verified") is True
                and a.get("evidence")
            ]
            if len(candidates) != 1:
                reject("UNIT_CONVERSION_EVIDENCE_REQUIRED")
                continue
            approved = candidates[0]
            try:
                if positive(approved.get("source_quantity")) != quantity:
                    raise ValueError()
                encoded = re.fullmatch(r"([A-Z]+)([0-9]+(?:\.[0-9]+)?)", raw_unit.upper())
                if not encoded or not approved.get("base_unit"):
                    raise ValueError()
                base = quantity * positive(encoded[2])
                quantity = positive(approved.get("supplier_quantity"))
                if base != quantity * positive(approved.get("base_per_supplier_unit")):
                    raise ValueError()
            except ValueError:
                reject("UNIT_CONVERSION_INCONSISTENT")
                continue
        sid = product.supplier_id
        if sid not in bindings:
            templates = db.query(SupplierTemplate).all()
            if sid in overrides:
                candidates = [t for t in templates if t.id == overrides[sid]]
            else:
                candidates = [
                    t for t in templates if t.supplier_id == sid or sid in (t.supplier_ids or [])
                ]
            candidates = [
                t
                for t in candidates
                if template_has_zone_config(t) and t.country_id in (None, order.country_id)
            ]
            if len(candidates) != 1:
                reject("TEMPLATE_BINDING_REQUIRED")
                continue
            template = candidates[0]
            columns = normalized_contract(template)["columns"]
            required = {"product_code", "quantity", "unit", "unit_price"}
            if not template.template_file_url or not required.issubset(set(columns.values())):
                reject("TEMPLATE_FIELDS_REQUIRED")
                continue
            bindings[sid] = template.id
        item = deepcopy(result)
        item.pop("inquiry_exclusion_code", None)
        item.pop("inquiry_exclusion_reason", None)
        item.update(
            rfq_quantity=float(quantity),
            rfq_unit=target_unit,
            source_quantity=original["quantity"],
            source_unit=raw_unit,
            conversion_evidence=approved or {"evidence": "相同订购单位，数量保持原值"},
        )
        prepared.append(item)
    # Keep verified preparation on safe rows even when sibling rows need
    # review. Arrangement matching reuses this evidence by line identity;
    # one bad product must not discard conversions already verified elsewhere.
    prepared_by_line = {item.get("line_id"): item for item in prepared}
    order.match_results = [
        prepared_by_line.get(result.get("line_id"), result) for result in results
    ]
    flag_modified(order, "match_results")
    return issues, bindings
