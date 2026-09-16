"""Inquiry state — read/serialize Inquiry + InquirySupplier ORM into v2-shaped dict.

Kept private to the domain. The orchestrator (Phase 5) writes through ORM
helpers; the HTTP layer reads via `service.read_inquiry_state()` →
`InquiryState.to_legacy_dict()` to feed the v2 frontend.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from domains.inquiry import repository as repo
from domains.inquiry.models import Inquiry, InquirySupplier
from domains.inquiry.schemas import (
    InquiryState,
    InquirySupplierState,
    TemplateBinding,
)


def _to_supplier_state(row: InquirySupplier) -> InquirySupplierState:
    template: TemplateBinding | None = None
    if row.template_id or row.template_name or row.template_selection_method:
        template = TemplateBinding(
            id=row.template_id,
            name=row.template_name,
            method=row.template_selection_method or "unavailable",
        )
    return InquirySupplierState(
        supplier_id=row.supplier_id,
        supplier_name=row.supplier_name,
        supplier_info=row.supplier_info,
        product_count=row.product_count,
        subtotal=row.subtotal,
        currency=row.currency,
        template=template,
        status=row.status,
        excel_file_url=row.excel_file_url,
        preview_html_url=row.preview_html_url,
        verify_results=row.verify_results,
        missing_fields=row.missing_fields,
        elapsed_seconds=row.elapsed_seconds,
        error_message=row.error_message,
    )


def to_state(db: Session, inquiry: Inquiry) -> InquiryState:
    rows = repo.list_inquiry_suppliers(db, inquiry.id)
    return InquiryState(
        id=inquiry.id,
        order_id=inquiry.order_id,
        group_id=inquiry.group_id,
        version=inquiry.version,
        status=inquiry.status,
        started_at=inquiry.started_at,
        completed_at=inquiry.completed_at,
        cancel_requested_at=inquiry.cancel_requested_at,
        heartbeat_at=inquiry.heartbeat_at,
        next_retry_at=inquiry.next_retry_at,
        run_attempts=inquiry.run_attempts,
        max_attempts=inquiry.max_attempts,
        supplier_count=inquiry.supplier_count,
        unassigned_count=inquiry.unassigned_count,
        total_elapsed_seconds=inquiry.total_elapsed_seconds,
        member_snapshot=inquiry.member_snapshot,
        unmatched_items=inquiry.unmatched_items,
        error_message=inquiry.error_message,
        suppliers=[_to_supplier_state(r) for r in rows],
    )


def ensure_inquiry(db: Session, *, order_id: int) -> Inquiry:
    """Return existing Inquiry row for an order, creating one if missing."""
    existing = repo.get_single_inquiry_by_order(db, order_id)
    if existing is not None:
        return existing
    inquiry = Inquiry(order_id=order_id, status="pending")
    db.add(inquiry)
    db.flush()
    return inquiry


def create_group_inquiry(
    db: Session,
    *,
    order_id: int,
    group_id: int,
    member_snapshot: list[dict[str, Any]],
    match_snapshot: list[dict[str, Any]],
    unmatched_items: list[dict[str, Any]],
) -> Inquiry:
    """Create, never overwrite, the next arrangement inquiry version."""
    latest = repo.get_latest_inquiry_by_group(db, group_id)
    inquiry = Inquiry(
        order_id=order_id,
        group_id=group_id,
        version=(latest.version + 1) if latest else 1,
        status="pending",
        member_snapshot=member_snapshot,
        match_snapshot=match_snapshot,
        unmatched_items=unmatched_items,
    )
    db.add(inquiry)
    db.flush()
    return inquiry


def reset_for_run(
    db: Session,
    inquiry: Inquiry,
    *,
    started_at: datetime,
    supplier_count: int,
    unassigned_count: int,
) -> None:
    """Mark an inquiry as starting a fresh run — clears completed/cancelled state."""
    inquiry.status = "in_progress"
    inquiry.started_at = started_at
    inquiry.completed_at = None
    inquiry.cancel_requested_at = None
    inquiry.heartbeat_at = started_at
    inquiry.next_retry_at = None
    inquiry.total_elapsed_seconds = None
    inquiry.error_message = None
    inquiry.supplier_count = supplier_count
    inquiry.unassigned_count = unassigned_count


def upsert_supplier(
    db: Session,
    *,
    inquiry_id: int,
    supplier_id: int,
    fields: dict[str, Any],
) -> InquirySupplier:
    row = repo.get_inquiry_supplier(db, inquiry_id, supplier_id)
    if row is None:
        row = InquirySupplier(inquiry_id=inquiry_id, supplier_id=supplier_id, **fields)
        db.add(row)
        db.flush()
        return row
    for k, v in fields.items():
        setattr(row, k, v)
    db.flush()
    return row
