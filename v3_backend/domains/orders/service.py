"""Orders service — single entry point for HTTP, Agent, CLI.

Kept thin: heavy lifting is delegated to `matching/`, `projection.py`,
`anomaly.py`. HTTP layer calls these functions; each function takes a
Session, validates input, coordinates, and returns plain dicts / ORM objects.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session, load_only
from sqlalchemy.orm.attributes import flag_modified

from domains.document import repository as doc_repo
from domains.identity import service as identity_service
from domains.masterdata import repository as md_repo
from domains.orders import anomaly, issues, repository
from domains.orders.errors import BadRequest, NotFound, StatusConflict
from domains.orders.matching import run_matching
from domains.orders.models import Order
from domains.orders.projection import project_purchase_order
from domains.orders.schemas import OrderDetail, OrderListItem, OrderUpdateRequest
from infrastructure.capabilities import CAP_FINANCIALS_VIEW

logger = logging.getLogger(__name__)


# ═════ Queries ═══════════════════════════════════════════════


def list_orders(
    db: Session,
    *,
    user_id: int,
    is_admin: bool,
    status: str | None = None,
    fulfillment_status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    total, items = repository.list_page(
        db,
        user_id=user_id,
        include_all_users=is_admin,
        status=status,
        fulfillment_status=fulfillment_status,
        limit=limit,
        offset=offset,
    )
    return {
        "total": total,
        "items": [_to_list_item(db, order).model_dump() for order in items],
    }


def get_order(db: Session, *, order_id: int, user_id: int, is_admin: bool) -> OrderDetail:
    order = _load_for_user(db, order_id, user_id, is_admin)
    return _to_detail(order, db, user_id)


def delete_order(db: Session, *, order_id: int, user_id: int, is_admin: bool) -> None:
    """Delete an Order and cascade through its dependents.

    The cascade chain (FK refs that PG enforces by default):
        InquirySupplier → Inquiry → Order

    Earlier code did `repository.delete(db, order)` directly, which crashed
    with `v3_inquiries_order_id_fkey` ForeignKeyViolation the moment any
    order had a generated inquiry. The exception escaped FastAPI's handler
    chain, so Cloud Run returned plain-text 500 without CORS headers — the
    browser then surfaced it as a misleading "Failed to fetch" error.

    Storage cleanup (Order PO file + each supplier's inquiry Excel) is
    best-effort: log + continue on failure. Orphaned blobs are cheap to GC
    later; an aborted delete is what the user actually feels.
    """
    # TD-1 — cascade the inquiry delete via inquiry.service so this
    # module no longer imports `Inquiry` / `InquirySupplier` directly.
    # The service handles the DB rows and returns the supplier inquiry
    # Excel URLs we need to clean from storage.
    from domains.inquiry import service as inquiry_service
    from infrastructure.storage import get_storage

    order = _load_for_user(db, order_id, user_id, is_admin)

    storage = get_storage()
    files_to_clean: list[str] = []
    if order.file_url:
        files_to_clean.append(order.file_url)

    files_to_clean.extend(
        inquiry_service.delete_inquiry_for_order(db, order.id)
    )

    repository.delete(db, order)

    for url in files_to_clean:
        try:
            storage.delete(url)
        except Exception as exc:
            logger.warning("delete_order: storage cleanup failed for %s: %s", url, exc)


def find_order_id_for_document(db: Session, document_id: int) -> int | None:
    """Cross-domain lookup: which Order (if any) was projected from this Document?

    The Document serializer in `domains.document` calls this so the document
    detail payload can carry `linked_order_id` for the frontend's "前往订单 #N"
    transition. Returns None while the automatic projection has not created
    an order yet, or when extraction/projection requires human recovery.
    """
    order = repository.get_by_document_id(db, document_id)
    return order.id if order else None


def find_order_ids_linked_to_document(db: Session, document_id: int) -> list[int]:
    """Return ALL Order IDs whose `document_id` matches.

    Used by the document delete path to either block the delete (when
    `force=False`) or unlink+delete the document (when `force=True`).
    Sister to `find_order_id_for_document` which only returns one row;
    in practice we rarely have multiple orders per document, but the
    delete path needs to be safe even if we ever do.

    Added 2026-06-16 (TD-1) so document.service stops importing
    orders.models.Order directly.
    """
    rows = db.query(Order).filter(Order.document_id == document_id).all()
    return [o.id for o in rows]


def find_order_group_meta(
    db: Session, group_id: int
) -> dict[str, str | None] | None:
    """Return the group's overlay fields (`ship_name`, `loading_date`)
    used by the inquiry orchestrator's merged-group flow.

    Why this exists: `domains.inquiry` used to `import OrderGroup` from
    `domains.orders.models` (RULE-3 cross-domain violation flagged by
    `check_arch`). This helper gives inquiry a model-free dict, so the
    direction of dependency stays "inquiry → orders.service".

    Returns None when the group does not exist. Doesn't auth-scope:
    callers are background jobs that already validated user ownership
    at the HTTP boundary.
    """
    # Lazy import so this module doesn't pull in the OrderGroup table
    # mapping at import time for unrelated callers.
    from domains.orders.models import OrderGroup

    g = db.get(OrderGroup, group_id)
    if g is None:
        return None
    return {"ship_name": g.ship_name, "loading_date": g.loading_date}


def match_order_group(db: Session, group_id: int) -> dict[str, Any]:
    """Public arrangement-level matcher used by the inquiry domain."""
    from domains.orders.groups.matching import match_arrangement

    return match_arrangement(db, group_id)


def unlink_orders_from_document(db: Session, order_ids: list[int]) -> None:
    """Set `document_id = NULL` on the given orders, then flush.

    Caller must commit. Flushing here (without commit) releases the FK
    reference so a subsequent `Document` delete in the same transaction
    doesn't trip the ForeignKeyViolation.
    """
    if not order_ids:
        return
    (
        db.query(Order)
        .filter(Order.id.in_(set(order_ids)))
        .update({Order.document_id: None}, synchronize_session=False)
    )
    db.flush()


# ═════ Create-from-document (connects Phase 2 stub) ═════════


def create_from_document(
    db: Session,
    *,
    document_id: int,
    user_id: int,
    is_admin: bool,
    force: bool = False,
) -> OrderDetail:
    """Create an Order from a Document and start matching.

    With `settings.ASYNC_CREATE_ORDER` (the default), this returns as
    soon as the Order row exists with `status="matching"`. The actual
    Gemini-backed matching pipeline runs in the background via
    `infrastructure.jobs.runner` so the HTTP call finishes in well under a second.
    The frontend navigates to the order detail page immediately; that
    page already polls `/orders/{id}` every 2s and surfaces
    `status="ready"` / `"error"` as soon as the worker flips it.

    With the flag off, we fall back to the legacy synchronous path that
    blocks on `run_matching` — kept as a 1-line revert in case the
    polling UI ever regresses.
    """
    from infrastructure.config import settings

    document = doc_repo.get(db, document_id)
    if document is None:
        raise NotFound("文档不存在")
    if not is_admin and document.user_id != user_id:
        raise NotFound("文档不存在")
    if document.status in ("uploaded", "extracting"):
        raise StatusConflict("文档尚在提取中，请稍后再试")
    if document.status == "error":
        raise BadRequest(document.processing_error or "文档提取失败")
    if not force and document.doc_type != "purchase_order":
        raise BadRequest("文档类型不是采购订单；传 force=true 强制创建")

    # Idempotency guard (2026-06-22): automatic projection and a legacy or
    # recovery request can race past the doc-type check before either commits
    # the projected Order. Without this guard, both paths could create
    # rows and downstream queries that assume "≤1 order per document"
    # hit `MultipleResultsFound` 500s. Return the existing order instead
    # — the frontend's create flow already handles the "redirect to this
    # order" path, so a double-submit lands the user on the same page
    # they'd expect.
    existing = repository.get_by_document_id(db, document_id)
    if existing is not None:
        return _to_detail(existing, db, user_id)

    # Projector handles both create + update; returns the Order row.
    # `run_match_inline=False` on the async path — matching is the
    # expensive Gemini call we're trying to move off the request thread,
    # and projection.py auto-runs it by default.
    order = project_purchase_order(
        document, db, run_match_inline=not settings.ASYNC_CREATE_ORDER
    )

    if not settings.ASYNC_CREATE_ORDER:
        # Legacy synchronous path. Kept behind the flag so we can revert
        # without re-deploying the polling UI.
        try:
            run_matching(order, db)
            order.status = "ready"
            repository.save(db, order)
        except Exception as exc:
            logger.exception("auto-match failed for order %s", order.id)
            order.processing_error = f"匹配失败: {exc}"
            order.status = "error"
            repository.save(db, order)
        return _to_detail(order, db, user_id)

    # Async path. Persist `status="matching"` synchronously so the
    # client sees a clean intermediate state on the immediate
    # navigation; the background task takes the order through to
    # "ready" / "error". Note we must commit *before* the task starts —
    # `_run_matching_for_order` opens its own SessionLocal and will not
    # see in-flight transactions on this request's session.
    order.status = "matching"
    order.processing_error = None
    repository.save(db, order)
    return _to_detail(order, db, user_id)


async def _run_matching_for_order(order_id: int) -> None:
    """Background job: run Gemini matching against an already-persisted
    Order and flip its status.

    Opens a fresh `SessionLocal` because the original HTTP request's
    session is already closed by the time this runs. Mirrors the
    contract of `domains.document.workflow.run_document_pipeline` so
    the existing `infrastructure.jobs.runner` schedules it uniformly.

    Errors are swallowed at the boundary (logged + recorded on
    `order.processing_error`) so a transient Gemini outage surfaces in
    the UI rather than escaping into the runner's "task failed" log.
    """
    import asyncio

    await asyncio.to_thread(_run_matching_for_order_sync, order_id)


def _run_matching_for_order_sync(order_id: int) -> None:
    # The same automatic continuation is used whether the Order was created
    # by the new upload pipeline or by the legacy explicit create endpoint.
    from domains.orders.automation import automatic_order_pipeline

    automatic_order_pipeline(order_id)


# ═════ Update / Rematch / Reprocess ═════════════════════════


_UPDATABLE_COLUMNS = {
    "po_number",
    "ship_name",
    "vendor_name",
    "delivery_date",
    "loading_date",
    "order_date",
    "currency",
    "destination_port",
    "country_id",
    "port_id",
}

# Columns whose values must be normalized to YYYY-MM-DD strings before
# saving. The model uses String(50) for these (TD-3 history: full Date
# type migration deferred), but we standardize the format at every
# write boundary so:
#   - lexicographic ORDER BY = real date order
#   - WHERE x >= '2026-06-01' works
#   - the UI sees one consistent format regardless of source
# Extraction already produces YYYY-MM-DD via the Gemini schema; this
# normalization protects the PATCH path where a user might paste
# '6/15/2026' or 'Jun 15, 2026'.
_DATE_COLUMNS_TO_NORMALIZE = {"delivery_date", "loading_date", "order_date"}

_PATCH_DATE_INPUT_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y.%m.%d",
    "%b %d, %Y",  # 'Jun 15, 2026'
    "%B %d, %Y",  # 'June 15, 2026'
)


def _normalize_patch_date(value: Any) -> Any:
    """Coerce common date string formats to canonical YYYY-MM-DD.

    Empty / None pass through unchanged. Unparseable strings also pass
    through — the field is String(50) so a free-form value isn't a hard
    error; we just stop trying to canonicalize it. Tightened format
    enforcement lives at the API schema layer if it's ever needed.
    """
    if value is None or value == "":
        return value
    if not isinstance(value, str):
        return value
    value = value.strip()
    for fmt in _PATCH_DATE_INPUT_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return value


def update_order(
    db: Session,
    *,
    order_id: int,
    user_id: int,
    is_admin: bool,
    body: OrderUpdateRequest,
) -> OrderDetail:
    order = _load_for_user(db, order_id, user_id, is_admin)
    data = body.model_dump(exclude_unset=True)

    # FK validation — mirrors rematch_order. Without these checks a
    # malformed body could write a dangling country_id/port_id, which
    # would then survive into the matcher's priority-0 lookup (added
    # 2026-05-29) and consistently return None for that field, silently
    # blocking valid product matches.
    if (
        "country_id" in data
        and data["country_id"] is not None
        and not md_repo.country_exists(db, data["country_id"])
    ):
        raise BadRequest("国家不存在")
    if (
        "port_id" in data
        and data["port_id"] is not None
        and not md_repo.port_exists(db, data["port_id"])
    ):
        raise BadRequest("港口不存在")

    for key, value in data.items():
        if key in _UPDATABLE_COLUMNS:
            if key in _DATE_COLUMNS_TO_NORMALIZE:
                value = _normalize_patch_date(value)
            setattr(order, key, value)

    if "products" in data and data["products"] is not None:
        order.products = list(data["products"])
        order.product_count = len(order.products)
        flag_modified(order, "products")

    if "order_metadata" in data and data["order_metadata"] is not None:
        existing = dict(order.order_metadata or {})
        existing.update(data["order_metadata"])
        order.order_metadata = existing
        flag_modified(order, "order_metadata")

    repository.save(db, order)
    from domains.orders.groups.automation import auto_group_order

    auto_group_order(db, order.id)
    db.refresh(order)
    return _to_detail(order, db, user_id)


def rematch_order(
    db: Session,
    *,
    order_id: int,
    user_id: int,
    is_admin: bool,
    country_id: int | None = None,
    port_id: int | None = None,
    delivery_date: str | None = None,
) -> OrderDetail:
    order = _load_for_user(db, order_id, user_id, is_admin)
    if country_id is not None:
        if not md_repo.country_exists(db, country_id):
            raise BadRequest("国家不存在")
        order.country_id = country_id
    if port_id is not None:
        if not md_repo.port_exists(db, port_id):
            raise BadRequest("港口不存在")
        order.port_id = port_id
    if delivery_date is not None:
        order.delivery_date = delivery_date

    order.status = "matching"
    repository.save(db, order)

    try:
        run_matching(order, db)
        order.status = "ready"
    except Exception as exc:
        order.processing_error = f"匹配失败: {exc}"
        order.status = "error"
    order.processed_at = datetime.utcnow()
    repository.save(db, order)
    from domains.orders.groups.automation import auto_group_order

    auto_group_order(db, order.id)
    db.refresh(order)
    return _to_detail(order, db, user_id)


def start_reprocess(
    db: Session, *, order_id: int, user_id: int, is_admin: bool
) -> OrderDetail:
    """Validate + transition to 'extracting'. Caller submits the background job.

    Idempotent: if order is already extracting/matching, returns current state
    without restarting. This prevents accidental duplicate Gemini calls when
    users double-click the button.
    """
    order = _load_for_user(db, order_id, user_id, is_admin)
    if order.document_id is None:
        raise BadRequest("订单未关联文档，无法重跑")
    document = doc_repo.get(db, order.document_id)
    if document is None:
        raise BadRequest("关联文档不存在")
    if order.status in ("extracting", "matching"):
        return _to_detail(order, db, user_id)
    order.status = "extracting"
    order.processing_error = None
    repository.save(db, order)
    return _to_detail(order, db, user_id)


def reprocess_order(db: Session, *, order_id: int, user_id: int, is_admin: bool) -> OrderDetail:
    """Re-run projection (from Document) + matching. Called from background job."""
    order = _load_for_user(db, order_id, user_id, is_admin)
    if order.document_id is None:
        raise BadRequest("订单未关联文档，无法重跑")
    document = doc_repo.get(db, order.document_id)
    if document is None:
        raise BadRequest("关联文档不存在")
    project_purchase_order(document, db)
    # Reload order (projection may have updated it)
    order = _load_for_user(db, order_id, user_id, is_admin)
    return rematch_order(db, order_id=order_id, user_id=user_id, is_admin=is_admin)


# ═════ Review / Anomaly / Financial ═════════════════════════


def mark_reviewed(
    db: Session,
    *,
    order_id: int,
    user_id: int,
    is_admin: bool,
    reviewer_id: int,
    notes: str | None = None,
) -> OrderDetail:
    order = _load_for_user(db, order_id, user_id, is_admin)
    order.is_reviewed = True
    order.reviewed_at = datetime.utcnow()
    order.reviewed_by = reviewer_id
    order.review_notes = notes
    repository.save(db, order)
    return _to_detail(order, db, user_id)


def run_anomaly_check(db: Session, *, order_id: int, user_id: int, is_admin: bool) -> OrderDetail:
    order = _load_for_user(db, order_id, user_id, is_admin)
    from domains.orders.automation import mark_pipeline_stage

    pipeline = (order.anomaly_data or {}).get("pipeline") or []
    latest_inquiry = None
    if order.group_id is not None:
        from domains.inquiry import service as inquiry_service

        states = inquiry_service.list_group_inquiry_states(db, order.group_id)
        latest_inquiry = states[0] if states else None
    if pipeline:
        mark_pipeline_stage(pipeline, 8, "running")
    result = anomaly.run_anomaly_check(
        order,
        inquiry=latest_inquiry,
        pipeline=pipeline,
    )
    if pipeline:
        mark_pipeline_stage(
            pipeline,
            8,
            "needs_review"
            if result["requires_human_review"]
            else "completed_with_warnings"
            if result["warning_count"]
            else "completed",
            evidence={
                "total": result["total_anomalies"],
                "warning": result["warning_count"],
                "error": result["error_count"],
                "blocking": result["blocking_count"],
            },
        )
        result["pipeline"] = pipeline
    order.anomaly_data = result
    flag_modified(order, "anomaly_data")
    repository.save(db, order)
    return _to_detail(order, db, user_id)


# ═════ Order payload (for Document detail page) ═════════════


def build_order_payload_for_document(
    db: Session,
    *,
    document_id: int,
    user_id: int,
    is_admin: bool,
) -> dict[str, Any]:
    """Return the v2-compatible order_payload blob for a Document.

    Called from `/api/documents/{id}/order-payload`. Uses the Order row if
    one exists; otherwise builds a "no Order yet, here's what would go in"
    preview from the Document's extracted_data.
    """
    document = doc_repo.get(db, document_id)
    if document is None:
        raise NotFound("文档不存在")
    if not is_admin and document.user_id != user_id:
        raise NotFound("文档不存在")

    order = repository.get_by_document_id(db, document_id)
    if order is not None:
        return _order_payload(order, document_id)

    # No Order yet — preview-only payload built from the Document's JSON.
    metadata = (document.extracted_data or {}).get("metadata") or {}
    products = (document.extracted_data or {}).get("products") or []
    required_fields = ("po_number", "ship_name", "delivery_date")
    missing = [field for field in required_fields if not metadata.get(field)]
    return {
        "document_id": document_id,
        "doc_type": document.doc_type,
        "order_metadata": dict(metadata),
        "products": list(products),
        "product_count": len(products),
        "missing_fields": missing,
        "blocking_missing_fields": missing + (["no_products"] if not products else []),
        "field_evidence": {},
        "confidence_summary": {
            "status": "ready" if not missing and products else "needs_review",
            "has_products": bool(products),
            "metadata_fields_present": sum(1 for f in required_fields if metadata.get(f)),
            "metadata_fields_required": len(required_fields),
        },
        "ready_for_order_creation": bool(not missing and products),
    }


def _order_payload(order: Order, document_id: int) -> dict[str, Any]:
    metadata = {
        "po_number": order.po_number,
        "ship_name": order.ship_name,
        "vendor_name": order.vendor_name,
        "delivery_date": order.delivery_date,
        "order_date": order.order_date,
        "currency": order.currency,
        "destination_port": order.destination_port,
        "total_amount": float(order.total_amount) if order.total_amount is not None else None,
    }
    required_fields = ("po_number", "ship_name", "delivery_date")
    missing = [field for field in required_fields if not metadata.get(field)]
    return {
        "document_id": document_id,
        "doc_type": "purchase_order",
        "order_metadata": metadata,
        "products": order.products or [],
        "product_count": order.product_count or 0,
        "missing_fields": missing,
        "blocking_missing_fields": missing,
        "field_evidence": order.field_evidence or {},
        "confidence_summary": {
            "status": "ready" if not missing and order.products else "needs_review",
            "has_products": bool(order.products),
            "metadata_fields_present": sum(1 for f in required_fields if metadata.get(f)),
            "metadata_fields_required": len(required_fields),
        },
        "ready_for_order_creation": False,  # order already exists
    }


# ═════ Helpers ═══════════════════════════════════════════════


def _load_for_user(db: Session, order_id: int, user_id: int, is_admin: bool) -> Order:
    order = repository.get(db, order_id)
    if order is None:
        raise NotFound("订单不存在")
    if not is_admin and order.user_id != user_id:
        raise NotFound("订单不存在")
    return order


def _to_detail(order: Order, db: Session, user_id: int) -> OrderDetail:
    """Serialize an Order to OrderDetail with flat PO metadata overlay.

    The ORM's `order_metadata` JSON is legacy storage; the canonical values
    now live in dedicated columns. We merge the two at serialization time so
    the v2 frontend (which reads `order_metadata.po_number` etc.) keeps working.
    """
    detail = OrderDetail.model_validate(order)
    related_orders = [order]
    if order.group_id is not None:
        related_query = db.query(Order).options(load_only(
            Order.id,
            Order.user_id,
            Order.group_id,
            Order.anomaly_data,
            Order.match_results,
        )).filter(Order.group_id == order.group_id)
        requesting_user = identity_service.get_business_user(db, user_id)
        if requesting_user.role != "superadmin":
            related_query = related_query.filter(Order.user_id == user_id)
        related_orders = related_query.all()
    detail.issue_overview = issues.build_issue_overview(order, related_orders)
    detail.actionable_count = detail.issue_overview["actionable_row_count"]
    if not identity_service.has_capability(db, user_id, CAP_FINANCIALS_VIEW):
        detail.financial_data = None
    flat = _flat_metadata(order, db)
    merged = dict(detail.order_metadata or {})
    # Column values take precedence over legacy JSON for the 8 fields
    for key, value in flat.items():
        if value is not None or key == "destination_port":
            merged[key] = value
    detail.order_metadata = merged
    return detail


def _to_list_item(db: Session, order: Order) -> OrderListItem:
    country_name = None
    if order.country_id:
        country = md_repo.get_country(db, order.country_id)
        country_name = country.name if country else None
    return OrderListItem(
        id=order.id,
        document_id=order.document_id,
        filename=order.filename,
        file_url=order.file_url,
        file_type=order.file_type,
        status=order.status,
        processing_error=order.processing_error,
        order_metadata=_flat_metadata(order, db),
        product_count=order.product_count or 0,
        total_amount=float(order.total_amount) if order.total_amount is not None else None,
        match_statistics=order.match_statistics,
        has_inquiry=order.inquiry_data is not None,
        is_reviewed=order.is_reviewed,
        fulfillment_status=order.fulfillment_status,
        template_id=order.template_id,
        country_name=country_name,
        template_match_method=order.template_match_method,
        group_id=order.group_id,
        loading_date=order.loading_date,
        created_at=order.created_at,
        updated_at=order.updated_at,
        processed_at=order.processed_at,
    )


def _flat_metadata(order: Order, db: Session) -> dict[str, Any]:
    """Return v2-compatible order_metadata shape (flat, single level)."""
    port = md_repo.get_port(db, order.port_id) if order.port_id else None
    return {
        "po_number": order.po_number,
        "ship_name": order.ship_name,
        "vendor_name": order.vendor_name,
        "delivery_date": order.delivery_date,
        "order_date": order.order_date,
        "currency": order.currency,
        "destination_port": port.name if port else None,
        "total_amount": float(order.total_amount) if order.total_amount is not None else None,
    }
