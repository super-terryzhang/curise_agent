"""Pure identity, sparse-patch and interval comparison rules."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

CLEAR_MARKER = "__CLEAR__"


def normalize_product_key(code: str, port_id: int) -> tuple[str, int]:
    normalized = str(code).strip().casefold()
    if not normalized or type(port_id) is not int or port_id <= 0:
        raise ValueError("产品代码和港口不能为空")
    return normalized, port_id


def apply_sparse_patch(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = dict(existing)
    for key, value in incoming.items():
        if value in (None, ""):
            continue
        result[key] = None if value == CLEAR_MARKER else value
    return result


def compare_price_period(
    existing: dict[str, Any], incoming: dict[str, Any]
) -> Literal["skip", "update", "overlap", "disjoint"]:
    old_start = _date(existing["effective_from"])
    old_end = _date(existing["effective_to"])
    new_start = _date(incoming["effective_from"])
    new_end = _date(incoming["effective_to"])
    if (old_start, old_end) == (new_start, new_end):
        old_amount = Decimal(str(existing["amount"]))
        new_amount = Decimal(str(incoming["amount"]))
        old_currency = str(existing.get("currency") or "").upper()
        new_currency = str(incoming.get("currency") or "").upper()
        return "skip" if (old_amount, old_currency) == (new_amount, new_currency) else "update"
    if new_start <= old_end and old_start <= new_end:
        return "overlap"
    return "disjoint"


def _date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
