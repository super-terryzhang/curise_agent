"""Stable read model for product-row issues on an order.

The anomaly engine records findings from several stages and, for grouped
orders, an inquiry can record a finding on a sibling PO.  This module turns
those records into one row-oriented response for the UI without changing the
stored source PO or guessing how a business exception should be resolved.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from domains.orders.models import Order

Finding = dict[str, Any]

_RESOLUTIONS: dict[str, tuple[str, str]] = {
    "PRODUCT_NOT_MATCHED": ("product_match", "修正或关联商品"),
    "EXACT_UNIQUE_MATCH_REQUIRED": ("product_match", "修正或关联商品"),
    "QUANTITY_INVALID": ("order_row", "修正 PO 本行"),
    "UNIT_REQUIRED": ("unit_conversion", "登记换算依据"),
    "UNIT_CONVERSION_REQUIRED": ("unit_conversion", "登记换算依据"),
    "UNIT_CONVERSION_INCONSISTENT": ("unit_conversion", "登记换算依据"),
    "SUPPLIER_REQUIRED": ("product_master", "编辑产品资料"),
    "PURCHASE_PRICE_PERIOD_MISSING": ("price_periods", "配置或核对价格期间"),
    "SELLING_PRICE_PERIOD_MISSING": ("price_periods", "配置或核对价格期间"),
    "SELLING_PRICE_DEVIATION": ("price_periods", "配置或核对价格期间"),
    "PO_NUMBER_REQUIRED": ("order", "编辑订单信息"),
    "LOADING_DATE_REQUIRED": ("order", "编辑订单信息"),
    "PORT_ID_REQUIRED": ("order", "编辑订单信息"),
    "ARRANGEMENT_REQUIRED": ("order", "编辑订单信息"),
    "DESTINATION_REQUIRES_REVIEW": ("order", "编辑订单信息"),
    "ORDER_TOTAL_MISMATCH": ("order", "编辑订单信息"),
    "TEMPLATE_BINDING_REQUIRED": ("template", "检查供应商与模板"),
    "TEMPLATE_FIELDS_REQUIRED": ("template", "检查供应商与模板"),
    "SUPPLIER_FILE_FAILED": ("template", "检查供应商与模板"),
}


def build_issue_overview(order: Order, related_orders: Iterable[Order]) -> dict[str, Any]:
    """Return deterministic issues for *order*, grouped by its product rows."""
    products = list(order.products or [])
    results = list(order.match_results or [])
    row_count = max(len(products), len(results))
    rows = [_build_row(index, products, results) for index in range(row_count)]

    findings = _collect_findings(order, related_orders)
    findings.extend(_unmatched_fallback_findings(rows, findings))
    row_findings, non_row_findings = _attach_findings(findings, rows)

    actionable_count = 0
    warning_count = 0
    for index, row in enumerate(rows):
        attached = row_findings[index]
        row["findings"] = attached
        row["inquiry_disposition"] = _inquiry_disposition(row, attached)
        row["inquiry_disposition_reason"] = _disposition_reason(row, attached)
        is_actionable = (
            row["match_status"] == "not_matched"
            or row["inquiry_disposition"] == "excluded"
            or any(item.get("severity") in {"error", "blocking"} for item in attached)
        )
        if is_actionable:
            actionable_count += 1
        if any(item.get("severity") == "warning" for item in attached):
            warning_count += 1

    # Some legacy/cross-PO snapshots retain stable line identities even when
    # the target Order predates stored product rows. They cannot be rendered as
    # a normal row, but must still contribute to the unique-row summary.
    unattached_actionable: set[tuple[Any, ...]] = set()
    unattached_warnings: set[tuple[Any, ...]] = set()
    for index, item in enumerate(non_row_findings):
        if item.get("scope") != "row":
            continue
        identity = _finding_identity(item, fallback=index)
        if item.get("severity") in {"error", "blocking"}:
            unattached_actionable.add(identity)
        elif item.get("severity") == "warning":
            unattached_warnings.add(identity)
    actionable_count += len(unattached_actionable)
    warning_count += len(unattached_warnings)

    return {
        "schema_version": 1,
        "actionable_row_count": actionable_count,
        "warning_row_count": warning_count,
        "rows": rows,
        "non_row_findings": non_row_findings,
    }


def _build_row(
    index: int,
    products: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    source = products[index] if index < len(products) else {}
    result = results[index] if index < len(results) else {}
    combined = {**source, **result}
    combined["row_index"] = index + 1
    combined["match_status"] = (
        "matched" if result.get("match_status") == "matched" else "not_matched"
    )
    return combined


def _collect_findings(order: Order, related_orders: Iterable[Order]) -> list[Finding]:
    candidates: list[Finding] = []

    for owner in related_orders:
        for item in (owner.anomaly_data or {}).get("findings") or []:
            if not isinstance(item, dict):
                continue
            source_order_id = item.get("source_order_id")
            belongs_to_target = _same_order_id(source_order_id, order.id)
            is_unattributed_target_finding = owner.id == order.id and source_order_id is None
            if belongs_to_target or is_unattributed_target_finding:
                candidates.append(item)

    unique: dict[tuple[Any, ...], Finding] = {}
    for item in candidates:
        enriched = dict(item)
        enriched["resolution"] = _resolution(enriched)
        key = (
            enriched.get("code"),
            enriched.get("source_order_id"),
            enriched.get("line_id") or enriched.get("source_line_id"),
            enriched.get("row_index"),
            enriched.get("field"),
            enriched.get("message") or enriched.get("description"),
        )
        unique.setdefault(key, enriched)
    return list(unique.values())


def _unmatched_fallback_findings(
    rows: list[dict[str, Any]], findings: list[Finding]
) -> list[Finding]:
    """Explain legacy unmatched rows even when no structured finding was stored."""
    name_counts = Counter(str(row.get("product_name") or "") for row in rows)
    existing_row_indices = {
        index + 1
        for item in findings
        if item.get("scope") == "row"
        and (index := _find_row_index(item, rows, name_counts)) is not None
    }
    result: list[Finding] = []
    for row in rows:
        row_index = int(row["row_index"])
        if row.get("match_status") == "matched" or row_index in existing_row_indices:
            continue
        result.append(
            {
                "code": "PRODUCT_NOT_MATCHED",
                "rule_version": 1,
                "step": 5,
                "severity": "error",
                "scope": "row",
                "category": "completeness",
                "field": None,
                "row_index": row_index,
                "line_id": row.get("line_id") or row.get("source_line_id"),
                "source_line": row.get("source_line") or row.get("line_number"),
                "page": row.get("page"),
                "product_code": row.get("product_code"),
                "product_name": row.get("product_name"),
                "source_order_id": row.get("source_order_id"),
                "source_po_number": row.get("source_po_number") or row.get("po_number"),
                "message": row.get("match_reason") or "未找到唯一匹配商品",
                "description": row.get("match_reason") or "未找到唯一匹配商品",
                "suggestion": "选择正确商品或补充产品主数据后重新匹配",
                "evidence": {"candidate_ids": row.get("candidate_ids") or []},
                "resolution": {
                    "target": "product_match",
                    "label": "修正或关联商品",
                },
            }
        )
    return result


def _attach_findings(
    findings: list[Finding], rows: list[dict[str, Any]]
) -> tuple[list[list[Finding]], list[Finding]]:
    attached: list[list[Finding]] = [[] for _ in rows]
    non_row: list[Finding] = []
    name_counts = Counter(str(row.get("product_name") or "") for row in rows)

    for item in findings:
        if item.get("scope") != "row":
            non_row.append(item)
            continue
        index = _find_row_index(item, rows, name_counts)
        if index is None:
            non_row.append(item)
        else:
            attached[index].append(item)
    return attached, non_row


def _find_row_index(
    finding: Finding,
    rows: list[dict[str, Any]],
    name_counts: Counter[str],
) -> int | None:
    identity_fields = (
        "line_id",
        "source_line_id",
        "arrangement_line_id",
        "source_line",
        "line_number",
    )
    for field in identity_fields:
        value = finding.get(field)
        if value is None:
            continue
        for index, row in enumerate(rows):
            if any(
                str(row.get(candidate)) == str(value)
                for candidate in identity_fields
                if row.get(candidate) is not None
            ):
                return index

    row_index = finding.get("row_index")
    try:
        numeric_index = int(row_index) - 1
    except (TypeError, ValueError):
        numeric_index = -1
    if 0 <= numeric_index < len(rows):
        return numeric_index

    name = str(finding.get("product_name") or "")
    if name and name_counts[name] == 1:
        return next(
            (index for index, row in enumerate(rows) if row.get("product_name") == name),
            None,
        )
    return None


def _resolution(item: Finding) -> dict[str, str]:
    code = str(item.get("code") or "").upper()
    if code.startswith("STEP_") or code.startswith("PIPELINE_"):
        target, label = "pipeline", "重新运行处理步骤"
    else:
        target, label = _RESOLUTIONS.get(code, ("review", "查看并人工处理"))
    return {"target": target, "label": label}


def _inquiry_disposition(row: dict[str, Any], findings: list[Finding]) -> str:
    if row.get("match_status") != "matched":
        return "excluded"
    if row.get("inquiry_eligibility") == "excluded":
        return "excluded"
    if any(item.get("severity") in {"error", "blocking"} for item in findings):
        return "excluded"
    if any(item.get("severity") == "warning" for item in findings):
        return "included_with_warning"
    return "included"


def _disposition_reason(row: dict[str, Any], findings: list[Finding]) -> str | None:
    if row.get("match_status") != "matched":
        return row.get("match_reason") or "商品尚未匹配"
    first_error = next(
        (item for item in findings if item.get("severity") in {"error", "blocking"}),
        None,
    )
    if first_error:
        return first_error.get("message") or first_error.get("description")
    if any(item.get("severity") == "warning" for item in findings):
        return "该商品可进入询价，但需要人工复核"
    return None


def _same_order_id(value: Any, order_id: int) -> bool:
    try:
        return int(value) == order_id
    except (TypeError, ValueError):
        return False


def _finding_identity(item: Finding, *, fallback: int) -> tuple[Any, ...]:
    for field in (
        "line_id",
        "source_line_id",
        "arrangement_line_id",
        "source_line",
        "line_number",
        "row_index",
    ):
        if item.get(field) is not None:
            return (field, str(item[field]))
    return ("unidentified-row", fallback)
