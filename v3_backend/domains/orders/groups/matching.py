"""Arrangement-level product matching with explicit PO-line provenance."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from domains.masterdata import service as masterdata_service
from domains.orders.groups.rules import normalized_date
from domains.orders.matching import code_first, llm_refine
from domains.orders.models import Order


class ArrangementMatchError(ValueError):
    """The arrangement is not deterministic enough for automatic matching."""


def match_arrangement(db: Session, group_id: int) -> dict[str, Any]:
    """Rebuild matching for every current PO in one supply arrangement.

    All rows share one selected-port/loading-day candidate pool. Rows are never
    aggregated: every result carries its Order, PO, Document and original-line
    identity so equal SKUs from different POs remain independently traceable.
    """
    orders = (
        db.query(Order)
        .filter(Order.group_id == group_id)
        .order_by(Order.created_at.asc(), Order.id.asc())
        .all()
    )
    if not orders:
        raise ArrangementMatchError("供船安排中没有订单")

    days = {_loading_day(order) for order in orders}
    if None in days:
        raise ArrangementMatchError("供船安排中存在缺少或无法识别装船日的订单")
    if len(days) != 1:
        raise ArrangementMatchError("供船安排中的订单装船日不一致")

    port_ids = {order.port_id for order in orders}
    if None in port_ids:
        raise ArrangementMatchError("供船安排中存在未选择目标港口的订单")
    if len(port_ids) != 1:
        raise ArrangementMatchError("供船安排中的目标港口不一致")

    loading_day = next(iter(days))
    port_id = next(iter(port_ids))
    port = masterdata_service.get_port(db, port_id)
    if port is None or not port.get("country_id"):
        raise ArrangementMatchError("所选目标港口不存在或没有国家信息")

    effective_at = datetime.combine(
        datetime.fromisoformat(loading_day).date(), datetime.min.time()
    )
    pool = code_first.load_candidate_pool(
        db,
        country_id=port["country_id"],
        port_id=port_id,
        # The existing matcher calls this parameter delivery_date; arrangement
        # matching deliberately uses the confirmed loading day per user rule.
        delivery_date=effective_at,
        price_date=effective_at,
    )

    inputs: list[dict[str, Any]] = []
    slices: list[tuple[Order, int, int]] = []
    previous_preparation: dict[str, dict[str, Any]] = {}
    for order in orders:
        start = len(inputs)
        po_number = order.po_number or (order.order_metadata or {}).get("po_number")
        for previous in order.match_results or []:
            key = previous.get("arrangement_line_id") or previous.get("line_id")
            if key:
                previous_preparation[f"{order.id}:{key}"] = previous
        for index, product in enumerate(order.products or [], start=1):
            if not isinstance(product, dict):
                continue
            source_line_id = product.get("line_id") or f"row-{index:05d}"
            inputs.append(
                {
                    **product,
                    "source_order_id": order.id,
                    "source_po_number": po_number,
                    # Existing supplier templates already know the `po_number`
                    # product field. Keep that contract while persisting the
                    # explicit source name used by the audit/UI layer.
                    "po_number": po_number,
                    "source_document_id": order.document_id,
                    "source_line_id": source_line_id,
                    "arrangement_line_id": f"order-{order.id}:{source_line_id}",
                }
            )
        slices.append((order, start, len(inputs)))

    results, unmatched = code_first.match_by_code(inputs, pool)
    llm_refine.apply_refinement(results, unmatched, pool)
    for result in results:
        previous = previous_preparation.get(
            f"{result.get('source_order_id')}:{result.get('arrangement_line_id')}"
        ) or previous_preparation.get(
            f"{result.get('source_order_id')}:{result.get('source_line_id')}"
        )
        if _same_prepared_match(previous, result):
            for field in (
                "rfq_quantity",
                "rfq_unit",
                "source_quantity",
                "source_unit",
                "conversion_evidence",
                "inquiry_exclusion_code",
                "inquiry_exclusion_reason",
            ):
                if field in previous:
                    result[field] = previous[field]
        if result.get("match_status") != "matched" and not result.get("match_reason"):
            result["match_reason"] = "未找到匹配商品"

    for order, start, end in slices:
        order_results = results[start:end]
        order.match_results = order_results
        order.match_statistics = _statistics(order_results)
        order.country_id = port["country_id"]
        order.processing_error = None

    db.commit()
    stats = _statistics(results)
    status = "completed" if stats["not_matched"] == 0 else "partial" if stats["matched"] else "unmatched"
    return {
        "group_id": group_id,
        "loading_date": loading_day,
        "port_id": port_id,
        "country_id": port["country_id"],
        "status": status,
        **stats,
        "items": results,
    }


def _loading_day(order: Order) -> str | None:
    metadata = order.order_metadata or {}
    return normalized_date(order.loading_date or metadata.get("loading_date"))


def _statistics(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    matched = sum(result.get("match_status") == "matched" for result in results)
    not_matched = total - matched
    return {
        "total": total,
        "matched": matched,
        "not_matched": not_matched,
        "match_rate": round(100 * matched / total, 1) if total else 0.0,
    }


def _same_prepared_match(
    previous: dict[str, Any] | None, current: dict[str, Any]
) -> bool:
    """Only reuse unit-conversion evidence when the exact DB match is unchanged."""
    if not previous or previous.get("match_status") != "matched":
        return False
    old_product = previous.get("matched_product") or {}
    new_product = current.get("matched_product") or {}
    return bool(old_product.get("id") and old_product.get("id") == new_product.get("id"))
