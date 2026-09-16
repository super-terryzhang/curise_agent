"""Strict source-backed matching for automated Oracle imports."""

from datetime import date, datetime

from domains.masterdata import Port
from domains.orders.matching.code_first import load_candidate_pool, match_by_code


def match_for_import(db, order):
    """Exact, unambiguous scope; no currency guess or fuzzy auto-selection."""
    destination = (order.destination_port or "").strip().upper()
    names = {destination}
    # Same explicit translation already verified by the scheduler adapter.
    if destination == "TOKYO":
        names.add("東京")
    ports = [p for p in db.query(Port).all() if p.name and p.name.strip().upper() in names]
    try:
        day = date.fromisoformat(order.delivery_date or "")
    except ValueError:
        return [{"code": "DELIVERY_DATE_REQUIRED"}]
    if len(ports) != 1 or not ports[0].country_id:
        return [{"code": "DESTINATION_REQUIRES_REVIEW"}]
    port = ports[0]
    order.port_id, order.country_id = port.id, port.country_id
    pool = load_candidate_pool(
        db,
        country_id=port.country_id,
        port_id=port.id,
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
