"""Extensible, deterministic anomaly detection for the eight-stage PO pipeline.

Rules are registered by stable code. Every finding identifies the stage and,
where applicable, the exact source row so safe rows can continue automatically.
The legacy category arrays remain in the response until the old order UI is gone.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from domains.orders.models import Order

PipelineStage = dict[str, Any]
Finding = dict[str, Any]


@dataclass(frozen=True)
class RuleContext:
    order: Order
    inquiry: Any | None = None
    pipeline: list[PipelineStage] | None = None


Rule = Callable[[RuleContext], list[Finding]]
_RULES: dict[str, Rule] = {}


def register_rule(code: str, rule: Rule, *, replace: bool = False) -> None:
    """Register an anomaly rule without changing the pipeline orchestrator."""
    normalized = code.strip().upper()
    if not normalized:
        raise ValueError("异常规则代码不能为空")
    if normalized in _RULES and not replace:
        raise ValueError(f"异常规则 {normalized} 已注册")
    _RULES[normalized] = rule


def unregister_rule(code: str) -> None:
    """Remove a dynamically registered rule (primarily for isolated tests)."""
    _RULES.pop(code.strip().upper(), None)


def list_rules() -> list[str]:
    return sorted(_RULES)


def rule(code: str):
    def decorate(fn: Rule) -> Rule:
        register_rule(code, fn)
        return fn

    return decorate


def run_anomaly_check(
    order: Order,
    *,
    inquiry: Any | None = None,
    pipeline: list[PipelineStage] | None = None,
    extra_findings: list[Finding] | None = None,
) -> dict[str, Any]:
    """Run every registered rule and return stable row/stage diagnostics."""
    context = RuleContext(order=order, inquiry=inquiry, pipeline=pipeline)
    findings = [finding for check in _RULES.values() for finding in check(context)]
    findings.extend(extra_findings or [])
    findings = _deduplicate(findings)
    price = [_legacy_item(item) for item in findings if item.get("category") == "price"]
    quantity = [
        _legacy_item(item) for item in findings if item.get("category") == "quantity"
    ]
    completeness = [
        _legacy_item(item)
        for item in findings
        if item.get("category") not in {"price", "quantity"}
    ]
    by_step = {
        str(step): [item for item in findings if item.get("step") == step]
        for step in range(1, 9)
    }
    severities = {
        level: sum(item.get("severity") == level for item in findings)
        for level in ("warning", "error", "blocking")
    }
    return {
        "schema_version": 2,
        "total_anomalies": len(findings),
        "anomaly_count": len(findings),
        "warning_count": severities["warning"],
        "error_count": severities["error"],
        "blocking_count": severities["blocking"],
        "requires_human_review": bool(severities["error"] or severities["blocking"]),
        "findings": findings,
        "anomalies": findings,
        "by_step": by_step,
        "pipeline": pipeline or [],
        "checked_rules": list_rules(),
        # Backward-compatible fields consumed by the current order page.
        "price_anomalies": price,
        "quantity_anomalies": quantity,
        "completeness_issues": completeness,
    }


def finding(
    *,
    code: str,
    step: int,
    severity: str,
    scope: str,
    message: str,
    suggestion: str,
    category: str = "completeness",
    row: dict[str, Any] | None = None,
    field: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> Finding:
    source = row or {}
    return {
        "code": code,
        "rule_version": 1,
        "step": step,
        "severity": severity,
        "scope": scope,
        "category": category,
        "field": field,
        "row_index": source.get("row_index"),
        "line_id": source.get("line_id") or source.get("source_line_id"),
        "source_line": source.get("source_line") or source.get("line_number"),
        "page": source.get("page"),
        "product_code": source.get("product_code"),
        "product_name": source.get("product_name"),
        "source_order_id": source.get("source_order_id"),
        "source_po_number": source.get("source_po_number") or source.get("po_number"),
        "message": message,
        "description": message,
        "suggestion": suggestion,
        "evidence": evidence or {},
    }


@rule("PIPELINE_STAGE_STATUS")
def _pipeline_stage_findings(context: RuleContext) -> list[Finding]:
    items: list[Finding] = []
    for stage in context.pipeline or []:
        status = stage.get("status")
        if status not in {"failed", "needs_review"}:
            continue
        items.append(
            finding(
                code=stage.get("error_code") or f"STEP_{stage.get('step')}_REVIEW",
                step=int(stage.get("step") or 8),
                severity="blocking" if status == "failed" else "error",
                scope=stage.get("scope") or "pipeline",
                message=stage.get("message") or f"{stage.get('name', '处理阶段')}需要人工处理",
                suggestion=stage.get("suggestion") or "检查该阶段输入后重新运行",
                evidence=stage.get("evidence") or {},
            )
        )
    return items


@rule("ORDER_REQUIRED_FIELDS")
def _required_fields(context: RuleContext) -> list[Finding]:
    checks = (
        ("po_number", context.order.po_number, 4, "订单号缺失", "核对 PO 原文件并补充订单号"),
        ("loading_date", context.order.loading_date, 6, "装船日缺失", "补充装船日后重新自动归组"),
        ("port_id", context.order.port_id, 6, "目标港口未确定", "选择唯一目标港口后重新自动归组"),
    )
    return [
        finding(
            code=f"{field.upper()}_REQUIRED",
            step=step,
            severity="error",
            scope="order",
            field=field,
            message=message,
            suggestion=suggestion,
        )
        for field, value, step, message, suggestion in checks
        if value in (None, "")
    ]


@rule("ROW_QUANTITY")
def _row_quantities(context: RuleContext) -> list[Finding]:
    items: list[Finding] = []
    for index, product in enumerate(context.order.products or [], start=1):
        row = {**product, "row_index": index}
        try:
            value = Decimal(str(product.get("quantity")))
            valid = value.is_finite() and value > 0
        except (InvalidOperation, TypeError, ValueError):
            valid = False
        if not valid:
            items.append(
                finding(
                    code="QUANTITY_INVALID",
                    step=5,
                    severity="error",
                    scope="row",
                    category="quantity",
                    field="quantity",
                    row=row,
                    message="数量必须是大于 0 的数字",
                    suggestion="核对 PO 原行并修正数量",
                    evidence={"value": product.get("quantity")},
                )
            )
    return items


@rule("ROW_MATCH_AND_PRICE")
def _row_matching(context: RuleContext) -> list[Finding]:
    items: list[Finding] = []
    products = context.order.products or []
    results = context.order.match_results or []
    for index in range(max(len(products), len(results))):
        source = products[index] if index < len(products) else {}
        result = results[index] if index < len(results) else {}
        row = {**source, **result, "row_index": index + 1}
        if result.get("match_status") != "matched":
            items.append(
                finding(
                    code="PRODUCT_NOT_MATCHED",
                    step=5,
                    severity="error",
                    scope="row",
                    row=row,
                    message=result.get("match_reason") or "未找到唯一匹配商品",
                    suggestion="选择正确商品或补充产品主数据后重新匹配",
                    evidence={"candidate_ids": result.get("candidate_ids") or []},
                )
            )
            continue
        matched = result.get("matched_product") or {}
        if not matched.get("supplier_id"):
            items.append(
                finding(
                    code="SUPPLIER_REQUIRED",
                    step=7,
                    severity="error",
                    scope="row",
                    row=row,
                    message="匹配商品未配置供应商",
                    suggestion="为商品配置供应商后重新生成询价版本",
                )
            )
        _append_price_period_findings(items, row, matched)
        _append_price_deviation(items, row, matched)
        source_unit = str(result.get("source_unit") or result.get("unit") or "").strip()
        supplier_unit = str(result.get("rfq_unit") or matched.get("unit") or "").strip()
        if source_unit and supplier_unit and source_unit.upper() != supplier_unit.upper():
            evidence = result.get("conversion_evidence")
            if not isinstance(evidence, dict) or not evidence.get("verified"):
                items.append(
                    finding(
                        code="UNIT_CONVERSION_REQUIRED",
                        step=7,
                        severity="error",
                        scope="row",
                        category="quantity",
                        row=row,
                        message=f"订购单位 {source_unit} 与供应商单位 {supplier_unit} 不一致",
                        suggestion="确认换算关系后重新生成询价版本",
                        evidence={"source_unit": source_unit, "supplier_unit": supplier_unit},
                    )
                )
    return items


@rule("ORDER_TOTAL")
def _order_total(context: RuleContext) -> list[Finding]:
    if context.order.total_amount is None:
        return []
    line_total = Decimal("0")
    for row in context.order.products or []:
        try:
            quantity = Decimal(str(row.get("quantity")))
            price = Decimal(str(row.get("unit_price")))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if quantity.is_finite() and price.is_finite():
            line_total += quantity * price
    expected = Decimal(str(context.order.total_amount))
    if expected <= 0 or line_total <= 0:
        return []
    deviation = abs(line_total - expected) / expected
    if deviation <= Decimal("0.05"):
        return []
    return [
        finding(
            code="ORDER_TOTAL_MISMATCH",
            step=3,
            severity="warning",
            scope="order",
            category="price",
            field="total_amount",
            message="商品行金额合计与订单总金额偏差超过 5%",
            suggestion="核对数量、客户单价和订单总金额",
            evidence={
                "line_total": float(line_total),
                "order_total": float(expected),
                "deviation": float(deviation),
            },
        )
    ]


@rule("INQUIRY_RESULT")
def _inquiry_result(context: RuleContext) -> list[Finding]:
    if context.inquiry is None:
        return []
    state = (
        context.inquiry.model_dump(mode="json")
        if hasattr(context.inquiry, "model_dump")
        else dict(context.inquiry)
    )
    items: list[Finding] = []
    for row in state.get("unmatched_items") or []:
        items.append(
            finding(
                code="RFQ_ROW_EXCLUDED",
                step=7,
                severity="error",
                scope="row",
                row=row,
                message=row.get("match_reason") or "该商品行未进入供应商询价文件",
                suggestion="处理匹配、供应商、单位或采购价问题后生成新版本",
            )
        )
    for supplier in state.get("suppliers") or []:
        if supplier.get("status") != "error":
            continue
        items.append(
            finding(
                code="SUPPLIER_FILE_FAILED",
                step=7,
                severity="error",
                scope="supplier",
                message=supplier.get("error_message") or "供应商询价文件生成失败",
                suggestion="检查供应商资料和模板后生成新版本",
                evidence={"supplier_id": supplier.get("supplier_id")},
            )
        )
    return items


def _append_price_period_findings(
    items: list[Finding], row: dict[str, Any], matched: dict[str, Any]
) -> None:
    for price_type, label in (("purchase", "采购价"), ("selling", "卖价")):
        period = matched.get(f"{price_type}_price_period") or {}
        warning = period.get("warning")
        if not warning:
            continue
        missing = period.get("source") == "period" and period.get("amount") is None
        items.append(
            finding(
                code=f"{price_type.upper()}_PRICE_PERIOD_MISSING",
                step=7 if price_type == "purchase" else 5,
                severity="error" if missing else "warning",
                scope="row",
                category="price",
                row=row,
                message=str(warning),
                suggestion=f"为装船日配置有效{label}区间",
                evidence=period,
            )
        )


def _append_price_deviation(
    items: list[Finding], row: dict[str, Any], matched: dict[str, Any]
) -> None:
    try:
        actual = Decimal(str(row.get("unit_price")))
        expected = Decimal(str(matched.get("contract_price")))
        if not all(value.is_finite() for value in (actual, expected)) or expected <= 0:
            return
    except (InvalidOperation, TypeError, ValueError):
        return
    ratio = actual / expected
    if Decimal("0.5") <= ratio <= Decimal("2"):
        return
    items.append(
        finding(
            code="SELLING_PRICE_DEVIATION",
            step=5,
            severity="warning",
            scope="row",
            category="price",
            row=row,
            message="客户 PO 单价与装船日有效卖价相差超过 2 倍",
            suggestion="核对客户 PO 单价或卖价期间",
            evidence={
                "actual_price": float(actual),
                "expected_price": float(expected),
                "ratio": round(float(ratio), 2),
                "selling_price_period": matched.get("selling_price_period"),
            },
        )
    )


def _legacy_item(item: Finding) -> Finding:
    evidence = item.get("evidence") or {}
    return {
        **item,
        "type": item.get("code"),
        "issue": item.get("message"),
        "description": item.get("message"),
        "actual_price": evidence.get("actual_price"),
        "expected_price": evidence.get("expected_price"),
        "deviation": evidence.get("ratio") or evidence.get("deviation"),
    }


def _deduplicate(items: list[Finding]) -> list[Finding]:
    unique: dict[tuple[Any, ...], Finding] = {}
    for item in items:
        key = (
            item.get("code"),
            item.get("step"),
            item.get("source_order_id"),
            item.get("line_id"),
            item.get("row_index"),
            item.get("field"),
            item.get("message"),
        )
        unique.setdefault(key, item)
    return list(unique.values())


__all__ = [
    "RuleContext",
    "finding",
    "list_rules",
    "register_rule",
    "run_anomaly_check",
    "unregister_rule",
]
