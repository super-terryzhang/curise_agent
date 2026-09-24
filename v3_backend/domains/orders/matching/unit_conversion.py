"""Apply verified masterdata unit rules to matched order rows."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from domains.masterdata import service as masterdata_service
from domains.orders.models import Order
from infrastructure.config import settings
from shared.numbers import decimal_to_json_value

logger = logging.getLogger(__name__)


def _manual_decision(result: dict[str, Any]) -> bool:
    evidence = result.get("conversion_evidence")
    if not isinstance(evidence, dict) or evidence.get("verified") is not True:
        return False
    if "rfq_quantity" not in result or not result.get("rfq_unit"):
        return False
    return evidence.get("scope_type") == "order_row" or evidence.get("rule_id") is None


def apply_verified_unit_conversions(
    order: Order,
    db: Session,
    results: list[dict[str, Any]],
    business_date: date | datetime | None,
) -> None:
    """Enrich matched rows in place while isolating failures to one row."""

    if not settings.UNIT_CONVERSION_RULES_ENABLED:
        return

    for index, result in enumerate(results):
        if result.get("match_status") != "matched":
            continue
        if _manual_decision(result):
            result.pop("unit_conversion_issue", None)
            continue

        for key in (
            "source_quantity",
            "source_unit",
            "rfq_quantity",
            "rfq_unit",
            "conversion_evidence",
            "unit_conversion_issue",
        ):
            result.pop(key, None)

        matched = result.get("matched_product") or {}
        source_quantity = result.get("quantity")
        source_unit = str(result.get("unit") or "")
        target_unit = str(matched.get("unit") or "")
        try:
            evaluated = masterdata_service.evaluate_unit_conversion(
                db,
                product_id=matched.get("id"),
                source_system="oracle",
                source_quantity=source_quantity,
                source_unit=source_unit,
                target_unit=target_unit,
                business_date=business_date,
                product_unit=matched.get("unit"),
                product_unit_size=matched.get("unit_size"),
                product_pack_size=matched.get("pack_size"),
            )
        except Exception:  # noqa: BLE001 - row isolation is the explicit boundary
            logger.exception(
                "unit conversion failed for order=%s row=%s product=%s",
                order.id,
                index + 1,
                matched.get("id"),
            )
            result["unit_conversion_issue"] = {
                "code": "UNIT_CONVERSION_EVALUATION_FAILED",
                "message": "单位换算检查暂时失败，请重新运行或人工处理",
                "evidence": {"row_index": index + 1},
            }
            continue

        if evaluated.get("status") in {"same", "converted"}:
            evidence = dict(evaluated.get("conversion_evidence") or {})
            evidence.update(
                source_unit=source_unit,
                target_unit=target_unit,
            )
            result.update(
                source_quantity=source_quantity,
                source_unit=source_unit,
                rfq_quantity=decimal_to_json_value(evaluated["target_quantity"]),
                rfq_unit=target_unit,
                conversion_evidence=evidence,
            )
            continue

        result["unit_conversion_issue"] = {
            "code": evaluated.get("issue_code") or "UNIT_CONVERSION_REQUIRED",
            "message": evaluated.get("message") or "单位换算需要人工确认",
            "evidence": evaluated.get("details") or {},
        }


__all__ = ["apply_verified_unit_conversions"]
