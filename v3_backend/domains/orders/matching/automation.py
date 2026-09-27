"""Strict source-backed product matching for automated Oracle imports."""

from datetime import date, datetime

from domains.masterdata import Port
from domains.orders.matching.code_first import load_candidate_pool, match_by_code


def match_for_import(db, order):
    """Match products inside an already validated country and port scope."""
    try:
        day = date.fromisoformat(order.delivery_date or "")
    except ValueError:
        return [{"code": "DELIVERY_DATE_REQUIRED"}]
    if order.port_id is None or order.country_id is None:
        return [{"code": "DESTINATION_REQUIRES_REVIEW"}]
    port = db.get(Port, order.port_id)
    if (
        port is None
        or port.status is not True
        or port.country_id is None
        or port.country_id != order.country_id
    ):
        return [{"code": "DESTINATION_REQUIRES_REVIEW"}]
    pool = load_candidate_pool(
        db,
        country_id=order.country_id,
        port_id=order.port_id,
        delivery_date=datetime.combine(day, datetime.min.time()),
    )
    results, _ = match_by_code(order.products or [], pool)
    order.match_results = results
    matched = sum(r.get("match_status") == "matched" for r in results)
    order.match_statistics = {
        "total": len(results),
        "matched": matched,
        "not_matched": len(results) - matched,
        "match_rate": round(100 * matched / len(results), 1) if results else 0,
    }
    return []
