"""Deterministic cross-sheet and database validation for staged imports."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from domains.dynamic_data.models import DataField, DataRecord
from domains.dynamic_data.validation import normalize_value
from domains.masterdata.models import Category, Country, Port, Product, ProductPricePeriod, Supplier

from .catalog import ProductCatalog, load_product_catalog
from .contracts import BatchCounts, BatchPreview, ImportIssue, PreviewRow
from .diff import CLEAR_MARKER, compare_price_period, normalize_product_key
from .models import ImportBatch, ImportRow, utc_now
from .template import PRICE_SHEET, PRODUCT_SHEET

CURRENCY = re.compile(r"^[A-Z]{3}$", re.ASCII)


def _issue(
    row: ImportRow | None,
    code: str,
    message: str,
    *,
    field: str | None = None,
    severity: str = "block",
    related_rows: list[tuple[str, int]] | None = None,
    existing_reference: str | None = None,
) -> ImportIssue:
    return ImportIssue(
        severity=severity,
        code=code,
        message=message,
        sheet=(PRODUCT_SHEET if row and row.sheet_key == "products" else PRICE_SHEET if row else None),
        row=row.source_row_number if row else None,
        field=field,
        related_rows=related_rows or [],
        existing_reference=existing_reference,
    )


def _name_index(rows) -> dict[str, list[Any]]:
    result: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        if row.name and str(row.name).strip():
            result[str(row.name).strip().casefold()].append(row)
    return result


def _reference(
    raw: Any,
    index: dict[str, list[Any]],
    row: ImportRow,
    *,
    label: str,
    required: bool,
    existing_id: int | None = None,
) -> tuple[int | None, list[ImportIssue]]:
    if raw in (None, ""):
        if existing_id is not None:
            return existing_id, []
        if required:
            return None, [_issue(row, f"MISSING_{label.upper()}", f"{label}不能为空", field=label)]
        return None, []
    if raw == CLEAR_MARKER:
        if required:
            return None, [_issue(row, f"MISSING_{label.upper()}", f"{label}不能为空", field=label)]
        return None, []
    matches = index.get(str(raw).strip().casefold(), [])
    if not matches:
        return None, [_issue(row, f"UNKNOWN_{label.upper()}", f"未找到启用的{label}：{raw}", field=label)]
    if len(matches) > 1:
        return None, [_issue(row, f"AMBIGUOUS_{label.upper()}", f"{label}名称不唯一：{raw}", field=label)]
    return matches[0].id, []


def _text(raw: Any, existing: str | None, *, required: bool, row, label):
    if raw in (None, ""):
        value = existing
    elif raw == CLEAR_MARKER:
        value = None
    elif isinstance(raw, str):
        value = raw.strip()
    else:
        return existing, [_issue(row, "INVALID_TEXT", f"{label}必须是文本", field=label)]
    if required and not value:
        return value, [_issue(row, "REQUIRED", f"{label}不能为空", field=label)]
    return value, []


def _extension_value(field: DataField, raw: Any, existing: Any, row: ImportRow, create: bool):
    if raw in (None, ""):
        raw = field.default_value if create else existing
    elif raw == CLEAR_MARKER:
        raw = None
    if field.field_type in {"single_select", "multi_select"} and raw is not None:
        options: dict[str, list[dict]] = defaultdict(list)
        options_by_id: dict[str, dict] = {}
        for option in field.config.get("options", []):
            if option.get("active", True):
                options[str(option["label"]).strip().casefold()].append(option)
                options_by_id[str(option["id"])] = option
        selections = (
            [part.strip() for part in re.split(r"[；;]", str(raw)) if part.strip()]
            if field.field_type == "multi_select"
            else [str(raw).strip()]
        )
        resolved = []
        for selection in selections:
            direct = options_by_id.get(selection)
            matches = [direct] if direct is not None else options.get(selection.casefold(), [])
            if len(matches) != 1:
                return existing, [
                    _issue(
                        row,
                        "UNKNOWN_OPTION" if not matches else "AMBIGUOUS_OPTION",
                        f"{field.label}选择项无效或不唯一：{selection}",
                        field=field.label,
                    )
                ]
            resolved.append(str(matches[0]["id"]))
        raw = resolved if field.field_type == "multi_select" else resolved[0]
    elif field.field_type == "boolean" and isinstance(raw, str):
        raw = {"是": True, "否": False}.get(raw.strip(), raw)
    try:
        return normalize_value(field, raw), []
    except Exception as exc:
        issues = getattr(exc, "issues", [])
        message = issues[0].message if issues else f"{field.label}格式不正确"
        code = issues[0].code if issues else "INVALID_EXTENSION_VALUE"
        return existing, [_issue(row, code, message, field=field.label)]


def _product_indexes(db: Session):
    products = list(db.scalars(select(Product).where(Product.status.is_(True))))
    mapping = {
        normalize_product_key(product.code, product.port_id): product
        for product in products
        if product.code and product.port_id
    }
    return products, mapping


def _normalize_product_row(
    row: ImportRow,
    catalog: ProductCatalog,
    product_map,
    products_by_id,
    indexes,
    anchors,
):
    raw = row.raw_values
    issues: list[ImportIssue] = []
    exported_id = raw.get("__product_id")
    exported_product = None
    if exported_id not in (None, ""):
        try:
            exported_product = products_by_id.get(int(exported_id))
        except (TypeError, ValueError):
            exported_product = None
        if exported_product is None:
            issues.append(_issue(row, "PRODUCT_ID_INVALID", "隐藏产品编号无效，请重新导出模板"))
    code, text_issues = _text(
        raw.get("product_code"),
        exported_product.code if exported_product else None,
        required=True,
        row=row,
        label="产品代码",
    )
    issues.extend(text_issues)
    port_id, reference_issues = _reference(
        raw.get("port"),
        indexes["port"],
        row,
        label="PORT",
        required=True,
        existing_id=exported_product.port_id if exported_product else None,
    )
    issues.extend(reference_issues)
    key = None
    existing = None
    if code and port_id:
        key = normalize_product_key(code, port_id)
        matched = product_map.get(key)
        if exported_product is not None:
            existing = exported_product
            if matched is not None and matched.id != exported_product.id:
                issues.append(
                    _issue(
                        row,
                        "PRODUCT_IDENTITY_CONFLICT",
                        "修改后的产品代码与港口已属于其他产品",
                        field="产品代码",
                    )
                )
        else:
            existing = matched
    core: dict[str, Any] = {}
    core["code"], item = _text(
        raw.get("product_code"), existing.code if existing else None, required=True, row=row, label="产品代码"
    )
    issues.extend(item)
    core["product_name_en"], item = _text(
        raw.get("product_name"),
        existing.product_name_en if existing else None,
        required=True,
        row=row,
        label="产品名称",
    )
    issues.extend(item)
    core["port_id"] = port_id
    for key_name, model_key, label, required in (
        ("supplier", "supplier_id", "SUPPLIER", False),
        ("category", "category_id", "CATEGORY", False),
        ("country", "country_id", "COUNTRY", True),
    ):
        current = getattr(existing, model_key) if existing else None
        core[model_key], item = _reference(
            raw.get(key_name), indexes[key_name], row, label=label, required=required, existing_id=current
        )
        issues.extend(item)
    for raw_key, model_key, label in (("unit", "unit", "单位"), ("brand", "brand", "品牌")):
        core[model_key], item = _text(
            raw.get(raw_key), getattr(existing, model_key) if existing else None, required=False, row=row, label=label
        )
        issues.extend(item)
    status = raw.get("status")
    if status in (None, ""):
        core["status"] = existing.status if existing else True
    elif status in ("启用", True):
        core["status"] = True
    elif status in ("停用", False):
        core["status"] = False
    else:
        core["status"] = existing.status if existing else True
        issues.append(_issue(row, "INVALID_STATUS", "状态只能填写启用或停用", field="状态"))
    if port_id and core.get("country_id"):
        port = next((item for item in indexes["port_rows"] if item.id == port_id), None)
        if port and port.country_id != core["country_id"]:
            issues.append(_issue(row, "PORT_COUNTRY_MISMATCH", "港口不属于所选国家", field="国家"))

    anchor = anchors.get(str(existing.id)) if existing else None
    if existing is not None and raw.get("__product_revision") not in (None, ""):
        try:
            exported_revision = int(raw["__product_revision"])
        except (TypeError, ValueError):
            exported_revision = -1
        if exported_revision != existing.revision:
            issues.append(
                _issue(row, "PRODUCT_CHANGED", "产品已被其他人修改，请重新导出模板")
            )
    if raw.get("__extension_revision") not in (None, ""):
        try:
            exported_extension_revision = int(raw["__extension_revision"])
        except (TypeError, ValueError):
            exported_extension_revision = -1
        if exported_extension_revision != (anchor.revision if anchor else 0):
            issues.append(
                _issue(row, "EXTENSION_CHANGED", "产品扩展信息已变化，请重新导出模板")
            )
    existing_extensions = dict(anchor.values) if anchor else {}
    extension_values = dict(existing_extensions)
    for field in catalog.extension_fields:
        field_key = f"extension:{field.id}"
        stored_key = str(field.id)
        raw_extension = raw.get(field_key)
        value, item = _extension_value(
            field,
            raw_extension,
            existing_extensions.get(stored_key),
            row,
            existing is None,
        )
        if (
            raw_extension not in (None, "")
            or stored_key in existing_extensions
            or (existing is None and field.default_value is not None)
        ):
            extension_values[stored_key] = value
        issues.extend(item)

    normalized = {"core_values": core, "extension_values": extension_values}
    if existing is None:
        action = "create"
    else:
        current_core = {key: getattr(existing, key) for key in core}
        action = "skip" if current_core == core and existing_extensions == extension_values else "update"
    snapshot = {
        "product_revision": existing.revision if existing else None,
        "extension_revision": anchor.revision if anchor else 0,
        "schema_version": catalog.table.schema_version,
        "before": (
            {
                "core_values": {
                    key: getattr(existing, key) for key in core
                },
                "extension_values": existing_extensions,
            }
            if existing
            else None
        ),
    }
    return key, existing, normalized, action, issues, snapshot


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    return datetime.fromisoformat(raw).date() if "T" in raw else date.fromisoformat(raw)


def _normalize_price_row(
    row, product_map, staged_products, port_index, periods_by_product, periods_by_id
):
    raw = row.raw_values
    issues: list[ImportIssue] = []
    code, item = _text(raw.get("product_code"), None, required=True, row=row, label="产品代码")
    issues.extend(item)
    port_id, item = _reference(raw.get("port"), port_index, row, label="PORT", required=True)
    issues.extend(item)
    key = normalize_product_key(code, port_id) if code and port_id else None
    existing_product = product_map.get(key) if key else None
    staged_product = staged_products.get(key) if key else None
    if existing_product is None and staged_product is None:
        issues.append(_issue(row, "PRODUCT_NOT_FOUND", "价格记录找不到唯一产品", field="产品代码"))
    raw_type = str(raw.get("price_type") or "").strip()
    price_type = {"采购价": "purchase", "卖价": "selling"}.get(raw_type)
    if price_type is None:
        issues.append(_issue(row, "INVALID_PRICE_TYPE", "价格类型只能填写采购价或卖价", field="价格类型"))
    try:
        amount = Decimal(str(raw.get("amount")))
        if not amount.is_finite() or amount < 0 or amount > Decimal("99999999.99"):
            raise ValueError
    except (InvalidOperation, ValueError):
        amount = Decimal(0)
        issues.append(_issue(row, "INVALID_AMOUNT", "价格必须是有效的非负数字", field="价格"))
    currency = str(raw.get("currency") or "").strip().upper() or None
    if currency is not None and not CURRENCY.fullmatch(currency):
        issues.append(_issue(row, "INVALID_CURRENCY", "币种必须是三个大写英文字母", field="币种"))
    try:
        start = _parse_date(raw.get("effective_from"))
        end = _parse_date(raw.get("effective_to"))
        if start > end:
            raise ValueError
    except (TypeError, ValueError):
        start = end = date.min
        issues.append(_issue(row, "INVALID_PERIOD", "开始日期必须早于或等于结束日期", field="开始日期"))
    normalized = {
        "product_code": code,
        "port_id": port_id,
        "price_type": price_type,
        "amount": str(amount),
        "currency": currency,
        "effective_from": start.isoformat(),
        "effective_to": end.isoformat(),
    }
    action, target, snapshot = "create", None, {}
    exported_period = None
    if raw.get("__period_id") not in (None, ""):
        try:
            exported_period = periods_by_id.get(int(raw["__period_id"]))
        except (TypeError, ValueError):
            exported_period = None
        if exported_period is None:
            issues.append(_issue(row, "PERIOD_ID_INVALID", "隐藏价格区间编号无效，请重新导出模板"))
    if existing_product and price_type and start != date.min:
        for period in periods_by_product.get((existing_product.id, price_type), []):
            comparison = compare_price_period(
                {
                    "amount": period.amount,
                    "currency": period.currency,
                    "effective_from": period.effective_from,
                    "effective_to": period.effective_to,
                },
                {
                    "amount": amount,
                    "currency": currency,
                    "effective_from": start,
                    "effective_to": end,
                },
            )
            if comparison in {"skip", "update"}:
                action, target = comparison, period
                snapshot = {
                    "period_revision": period.revision,
                    "before": {
                        "product_code": code,
                        "port_id": port_id,
                        "price_type": period.price_type,
                        "amount": str(period.amount),
                        "currency": period.currency,
                        "effective_from": period.effective_from.isoformat(),
                        "effective_to": period.effective_to.isoformat(),
                    },
                }
                break
            if comparison == "overlap":
                issues.append(
                    _issue(
                        row,
                        "DATABASE_PERIOD_OVERLAP",
                        "与数据库已有价格区间部分重叠",
                        field="开始日期",
                        existing_reference=(
                            f"{period.effective_from.isoformat()}—{period.effective_to.isoformat()}"
                        ),
                    )
                )
    if exported_period is not None:
        if (
            existing_product is None
            or exported_period.product_id != existing_product.id
            or exported_period.price_type != price_type
        ):
            issues.append(
                _issue(row, "PERIOD_ID_MISMATCH", "价格区间不属于当前产品或价格类型")
            )
        else:
            target = exported_period
            comparison = compare_price_period(
                {
                    "amount": exported_period.amount,
                    "currency": exported_period.currency,
                    "effective_from": exported_period.effective_from,
                    "effective_to": exported_period.effective_to,
                },
                {
                    "amount": amount,
                    "currency": currency,
                    "effective_from": start,
                    "effective_to": end,
                },
            )
            action = "skip" if comparison == "skip" else "update"
            snapshot = {
                "period_revision": exported_period.revision,
                "before": {
                    "product_code": code,
                    "port_id": port_id,
                    "price_type": exported_period.price_type,
                    "amount": str(exported_period.amount),
                    "currency": exported_period.currency,
                    "effective_from": exported_period.effective_from.isoformat(),
                    "effective_to": exported_period.effective_to.isoformat(),
                },
            }
            if raw.get("__period_revision") not in (None, ""):
                try:
                    exported_revision = int(raw["__period_revision"])
                except (TypeError, ValueError):
                    exported_revision = -1
                if exported_revision != exported_period.revision:
                    issues.append(
                        _issue(row, "PERIOD_CHANGED", "价格区间已变化，请重新导出模板")
                    )
    return key, existing_product, normalized, action, target, issues, snapshot


def validate_batch(db: Session, batch_id: UUID, user_id: int) -> BatchPreview:
    batch = db.get(ImportBatch, batch_id)
    if batch is None or batch.user_id != user_id:
        raise ValueError("导入批次不存在")
    if batch.status in {"committed", "rolled_back", "cancelled"}:
        raise ValueError("当前批次已结束，不能重新检查")
    rows = list(
        db.scalars(
            select(ImportRow)
            .where(ImportRow.batch_id == batch.id)
            .order_by(ImportRow.sheet_key, ImportRow.source_row_number)
        )
    )
    catalog = load_product_catalog(db)
    global_issues = [ImportIssue.model_validate(item) for item in (batch.result or {}).get("issues", [])]
    if batch.product_schema_version != catalog.table.schema_version and not any(
        issue.code == "STALE_SCHEMA" for issue in global_issues
    ):
        global_issues.append(
            _issue(None, "STALE_SCHEMA", "产品字段结构已变化，请下载最新模板")
        )

    countries = list(db.scalars(select(Country).where(Country.status.is_(True))))
    ports = list(db.scalars(select(Port).where(Port.status.is_(True))))
    suppliers = list(db.scalars(select(Supplier).where(Supplier.status.is_(True))))
    categories = list(db.scalars(select(Category).where(Category.status.is_(True))))
    indexes = {
        "country": _name_index(countries),
        "port": _name_index(ports),
        "supplier": _name_index(suppliers),
        "category": _name_index(categories),
        "port_rows": ports,
    }
    products, product_map = _product_indexes(db)
    products_by_id = {product.id: product for product in products}
    anchors = {
        record.source_record_id: record
        for record in db.scalars(
            select(DataRecord).where(
                DataRecord.table_id == catalog.table.id, DataRecord.status == "active"
            )
        )
        if record.source_record_id is not None
    }
    periods = list(db.scalars(select(ProductPricePeriod).where(ProductPricePeriod.status.is_(True))))
    periods_by_product: dict[tuple[int, str], list[ProductPricePeriod]] = defaultdict(list)
    for period in periods:
        periods_by_product[(period.product_id, period.price_type)].append(period)
    periods_by_id = {period.id: period for period in periods}

    product_rows = [row for row in rows if row.sheet_key == "products"]
    price_rows = [row for row in rows if row.sheet_key == "prices"]
    staged_products: dict[tuple[str, int], ImportRow] = {}
    for row in product_rows:
        parser_issues = [ImportIssue.model_validate(issue) for issue in row.issues]
        result = _normalize_product_row(
            row, catalog, product_map, products_by_id, indexes, anchors
        )
        key, existing, normalized, action, issues, snapshot = result
        row.normalized_values = normalized
        row.product_code_normalized = key[0] if key else None
        row.port_id = key[1] if key else None
        row.target_product_id = existing.id if existing else None
        row.snapshot = snapshot
        row.action = "block" if any(i.severity == "block" for i in [*parser_issues, *issues]) else action
        row.issues = [i.model_dump(mode="json") for i in [*parser_issues, *issues]]
        if key and key not in staged_products:
            staged_products[key] = row

    groups: dict[tuple[str, int], list[ImportRow]] = defaultdict(list)
    for row in product_rows:
        if row.product_code_normalized and row.port_id:
            groups[(row.product_code_normalized, row.port_id)].append(row)
    for duplicate_rows in groups.values():
        if len(duplicate_rows) < 2:
            continue
        related = [(PRODUCT_SHEET, item.source_row_number) for item in duplicate_rows]
        payloads = [item.normalized_values for item in duplicate_rows]
        if all(payload == payloads[0] for payload in payloads[1:]):
            for item in duplicate_rows[1:]:
                item.action = "skip"
                item.issues = [
                    *item.issues,
                    _issue(
                        item,
                        "DUPLICATE_IDENTICAL_PRODUCT",
                        "同一文件中产品资料重复且内容相同，本行跳过",
                        severity="warning",
                        related_rows=related,
                    ).model_dump(mode="json"),
                ]
        else:
            for item in duplicate_rows:
                item.action = "block"
                item.issues = [
                    *item.issues,
                    _issue(
                        item,
                        "DUPLICATE_PRODUCT",
                        "同一文件中相同产品代码与港口的资料不一致",
                        related_rows=related,
                    ).model_dump(mode="json"),
                ]

    for row in price_rows:
        parser_issues = [ImportIssue.model_validate(issue) for issue in row.issues]
        result = _normalize_price_row(
            row,
            product_map,
            staged_products,
            indexes["port"],
            periods_by_product,
            periods_by_id,
        )
        key, existing_product, normalized, action, target, issues, snapshot = result
        row.normalized_values = normalized
        row.product_code_normalized = key[0] if key else None
        row.port_id = key[1] if key else None
        row.target_product_id = existing_product.id if existing_product else None
        row.target_period_id = target.id if target else None
        row.snapshot = snapshot
        row.action = "block" if any(i.severity == "block" for i in [*parser_issues, *issues]) else action
        row.issues = [i.model_dump(mode="json") for i in [*parser_issues, *issues]]

    price_groups: dict[tuple, list[ImportRow]] = defaultdict(list)
    for row in price_rows:
        values = row.normalized_values
        if row.product_code_normalized and row.port_id and values.get("price_type"):
            price_groups[(row.product_code_normalized, row.port_id, values["price_type"])].append(row)
    for group in price_groups.values():
        for index, left in enumerate(group):
            left_start = date.fromisoformat(left.normalized_values["effective_from"])
            left_end = date.fromisoformat(left.normalized_values["effective_to"])
            if left_start == date.min:
                continue
            for right in group[index + 1 :]:
                right_start = date.fromisoformat(right.normalized_values["effective_from"])
                right_end = date.fromisoformat(right.normalized_values["effective_to"])
                if right_start <= left_end and left_start <= right_end:
                    related = [
                        (PRICE_SHEET, left.source_row_number),
                        (PRICE_SHEET, right.source_row_number),
                    ]
                    for item in (left, right):
                        item.action = "block"
                        item.issues = [
                            *item.issues,
                            _issue(
                                item,
                                "FILE_PERIOD_OVERLAP",
                                "同一文件中的价格区间互相重叠",
                                field="开始日期",
                                related_rows=related,
                            ).model_dump(mode="json"),
                        ]

    price_types = defaultdict(set)
    for period in periods:
        product = db.get(Product, period.product_id)
        if product and product.code and product.port_id:
            price_types[normalize_product_key(product.code, product.port_id)].add(period.price_type)
    for row in price_rows:
        if row.action != "block" and row.product_code_normalized and row.port_id:
            price_types[(row.product_code_normalized, row.port_id)].add(
                row.normalized_values.get("price_type")
            )
    for row in product_rows:
        if not row.product_code_normalized or not row.port_id:
            continue
        available = price_types[(row.product_code_normalized, row.port_id)]
        warnings = []
        if "purchase" not in available:
            warnings.append(
                _issue(
                    row,
                    "MISSING_PURCHASE_PRICE",
                    "采购价未配置；允许导入，但后续订单需要复核",
                    severity="warning",
                )
            )
        if "selling" not in available:
            warnings.append(
                _issue(
                    row,
                    "MISSING_SELLING_PRICE",
                    "卖价未配置；允许导入，但后续订单需要复核",
                    severity="warning",
                )
            )
        row.issues = [*row.issues, *(issue.model_dump(mode="json") for issue in warnings)]

    previews = [
        PreviewRow(
            id=row.id,
            sheet=PRODUCT_SHEET if row.sheet_key == "products" else PRICE_SHEET,
            row=row.source_row_number,
            action=row.action,
            product_key=(
                f"{row.product_code_normalized}@{row.port_id}"
                if row.product_code_normalized and row.port_id
                else None
            ),
            target_id=row.target_period_id if row.sheet_key == "prices" else row.target_product_id,
            normalized_values=row.normalized_values,
            issues=[ImportIssue.model_validate(issue) for issue in row.issues],
        )
        for row in rows
    ]
    counts = BatchCounts(
        create=sum(row.action == "create" for row in rows),
        update=sum(row.action == "update" for row in rows),
        skip=sum(row.action == "skip" for row in rows),
        warning=sum(any(issue.severity == "warning" for issue in preview.issues) for preview in previews),
        block=sum(row.action == "block" for row in rows) + sum(i.severity == "block" for i in global_issues),
    )
    status = "failed" if counts.block else "ready"
    batch.status = status
    batch.create_count = counts.create
    batch.update_count = counts.update
    batch.skip_count = counts.skip
    batch.warning_count = counts.warning
    batch.block_count = counts.block
    batch.validated_at = utc_now()
    batch.result = {
        "issues": [issue.model_dump(mode="json") for issue in global_issues],
        "counts": counts.model_dump(mode="json"),
    }
    batch.error_message = "检查发现阻止项，请修正后重新上传" if counts.block else None
    db.commit()
    return BatchPreview(
        batch_id=batch.id,
        status=status,
        counts=counts,
        rows=previews,
        issues=global_issues,
    )
