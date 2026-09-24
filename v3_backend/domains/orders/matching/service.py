"""Matching pipeline orchestrator — `run_matching(order, db) -> result dict`."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from domains.orders.matching import code_first, geo, llm_refine
from domains.orders.matching.unit_conversion import apply_verified_unit_conversions
from domains.orders.models import Order

logger = logging.getLogger(__name__)


_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%d-%m-%Y",
    "%Y-%m-%dT%H:%M:%S",
)


def run_matching(order: Order, db: Session) -> dict[str, Any]:
    """Three-stage matching: geo → code-first → LLM refine.

    Updates `order` in place (country_id, port_id, match_results, match_statistics).
    Caller is responsible for committing the transaction.
    """
    start = time.perf_counter()

    # Stage 1: Resolve country / port / delivery date from order hints
    geo_result = geo.resolve_geo(order, db)
    country_id = geo_result.get("country_id")
    port_id = geo_result.get("port_id")
    delivery_date = geo_result.get("delivery_date")
    delivery_dt = _parse_date(str(delivery_date)) if delivery_date else None
    loading_date = order.loading_date or (order.order_metadata or {}).get("loading_date")
    loading_dt = _parse_date(str(loading_date)) if loading_date else None

    if country_id is not None:
        order.country_id = int(country_id) if isinstance(country_id, int) else None
    if port_id is not None:
        order.port_id = int(port_id) if isinstance(port_id, int) else None
    if delivery_date:
        order.delivery_date = str(delivery_date)

    # No delivery date → we can still code-match but flag it upstream
    skipped_reason: str | None = None
    if delivery_dt is None:
        skipped_reason = "missing_delivery_date"

    # Stage 2: Load Products pool + code-first match
    inputs = list(order.products or [])
    pool = code_first.load_candidate_pool(
        db,
        country_id=order.country_id,
        port_id=order.port_id,
        delivery_date=delivery_dt,
        # Both commercial prices use loading day. Product availability keeps
        # its pre-existing delivery-date filter above.
        price_date=loading_dt,
    )
    all_results, unmatched = code_first.match_by_code(inputs, pool)

    # Stage 3: LLM fuzzy refinement (Fake / Gemini)
    llm_refine.apply_refinement(all_results, unmatched, pool)

    # Stage 4: Apply only verified, exact-scope unit conversion rules. The
    # coordinator isolates failures to a row and is reversible by feature flag.
    apply_verified_unit_conversions(
        order,
        db,
        all_results,
        delivery_dt.date() if delivery_dt is not None else None,
    )

    stats = _summarize(all_results)
    order.match_results = all_results
    order.match_statistics = stats
    order.processing_error = skipped_reason

    elapsed = time.perf_counter() - start
    logger.info(
        "order %s matching done in %.2fs: %d/%d matched (%s)",
        order.id,
        elapsed,
        stats["matched"],
        stats["total"],
        stats["match_rate"],
    )

    return {
        "country_id": order.country_id,
        "port_id": order.port_id,
        "delivery_date": order.delivery_date,
        "match_results": all_results,
        "statistics": stats,
        "skipped_reason": skipped_reason,
    }


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    matched = sum(1 for r in results if r["match_status"] == "matched")
    not_matched = total - matched
    rate = round(matched / total * 100, 1) if total else 0.0
    return {"total": total, "matched": matched, "not_matched": not_matched, "match_rate": rate}
