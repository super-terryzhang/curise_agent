"""Inquiry ORM — SupplierTemplate (Phase 4) + Inquiry / InquirySupplier (Phase 5).

Tables:
- `v2_supplier_templates` — Phase 4. Shared with v2.
- `v3_inquiries` — Phase 5. NEW. One row per Order that has been kicked off
  for inquiry generation. v2 stored this state inside `Order.inquiry_data`
  JSON; v3 expands it into a relational table while continuing to serialize
  the v2-shaped JSON for the frontend (ADR-0004 expand-only).
- `v3_inquiry_suppliers` — Phase 5. NEW. One row per supplier per Inquiry —
  carries status, file URL, verify results, elapsed time, error message.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.db.base import Base


class SupplierTemplate(Base):
    __tablename__ = "v2_supplier_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # Logical FKs (no DB constraint — kept loose to match v2)
    supplier_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supplier_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    country_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    template_name: Mapped[str] = mapped_column(String(200), nullable=False)
    template_file_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Field positions: {"po_number": {"position": "A4", "data_type": "string"}, ...}
    field_positions: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    has_product_table: Mapped[bool] = mapped_column(Boolean, default=True)
    product_table_config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    order_format_template_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    field_mapping_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    template_styles: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


# ─── Phase 5 ──────────────────────────────────────────────────


class Inquiry(Base):
    """Top-level state for one inquiry version.

    Legacy single-order runs keep ``group_id`` empty. Arrangement runs create
    a new row for every generation so their inputs and outputs remain auditable.
    ``order_id`` remains the compatibility anchor for the existing endpoints.
    """

    __tablename__ = "v3_inquiries"
    __table_args__ = (
        UniqueConstraint("group_id", "version", name="uq_v3_inquiries_group_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("v2_orders.id"), nullable=False, index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("v3_order_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Immutable arrangement input/output evidence captured for this version.
    member_snapshot: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    match_snapshot: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    unmatched_items: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # pending | in_progress | completed | partial | unmatched | cancelled | error
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    run_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)

    supplier_count: Mapped[int] = mapped_column(Integer, default=0)
    unassigned_count: Mapped[int] = mapped_column(Integer, default=0)
    total_elapsed_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class InquirySupplier(Base):
    """Per-supplier inquiry state (N:1 Inquiry)."""

    __tablename__ = "v3_inquiry_suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    inquiry_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("v3_inquiries.id"), nullable=False, index=True
    )

    # Logical FK to suppliers — loose to match v2 conventions
    supplier_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    supplier_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    supplier_info: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    product_count: Mapped[int] = mapped_column(Integer, default=0)
    subtotal: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Logical FK to v2_supplier_templates
    template_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    template_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # exact | candidate_auto | user_selected | candidates | unavailable
    template_selection_method: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # pending | generating | completed | error | cancelled
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)

    excel_file_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    preview_html_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    verify_results: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    missing_fields: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    elapsed_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
