"""Order ORM — the purchase_order subtype of Document (ADR-0001).

Table `v2_orders` is shared with v2 production. The new columns at the
bottom (`po_number`, `ship_name`, `vendor_name`, `order_date`, `currency`,
`destination_port`, `field_evidence`) are added by Alembic migration 002
(expand-only). Before the migration runs, v2 stores these values inside
the JSON field `order_metadata`; after cutover, v3 reads them from the
new columns directly.

Old v2 fields (`extraction_data`, `order_metadata`, `products`) stay on
this model for backward compatibility — the migration is expand, not
replace. Contract (drop of old fields) happens after Phase 7 cutover.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.db.base import Base


class Order(Base):
    __tablename__ = "v2_orders"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    document_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("v2_documents.id"), nullable=True, index=True
    )

    # File metadata (from Document at creation time)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_type: Mapped[str] = mapped_column(String(10), default="pdf")

    # Pipeline state
    status: Mapped[str] = mapped_column(String(20), default="uploading")
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # FK to masterdata (resolved by matching pipeline)
    country_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    port_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Legacy JSON blobs (kept for backward compatibility with v2)
    extraction_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    order_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    products: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    product_count: Mapped[int] = mapped_column(Integer, default=0)
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    # Matching + analysis results
    match_results: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    match_statistics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    anomaly_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    financial_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Phase 5 (inquiry) payload
    inquiry_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Review
    is_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Fulfillment (Phase 4-5)
    fulfillment_status: Mapped[str] = mapped_column(String(30), default="pending")
    delivery_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    invoice_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    invoice_date: Mapped[str | None] = mapped_column(String(50), nullable=True)
    payment_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    payment_date: Mapped[str | None] = mapped_column(String(50), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    attachments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, default=list)
    fulfillment_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_environment: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Template hint (Phase 4)
    template_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    template_match_method: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # ─── Expanded PO columns (migration 002 — ADR-0001) ───────────
    # These are the fields Phase 3 promotes out of `order_metadata` JSON
    # into proper columns. `delivery_date` already exists below as a string.
    po_number: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    ship_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    vendor_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    order_date: Mapped[str | None] = mapped_column(String(50), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    destination_port: Mapped[str | None] = mapped_column(String(200), nullable=True)
    field_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # delivery_date is a String(50) to preserve the raw value from the document
    delivery_date: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # loading_date — when supplies are loaded onto the cruise ship.
    # Distinct from delivery_date (when supplier ships to port). Added per
    # Felix 2026-06-12 request for the order list page. Same String(50)
    # convention as delivery_date; extracted by Gemini into YYYY-MM-DD
    # format via `_llm_extractor._RESPONSE_SCHEMA`. Migration: 0013.
    loading_date: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ─── Financial (Phase 7 redesign, migration 0012) ───────────
    # Preferred currency for the P&L view. NULL → fall back to `currency`
    # (the order's PO currency) at compute time.
    display_currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Per-order overridable tax rate. Applied as `tax_rate * gross_profit`
    # in `compute_pnl`. Default 0.06 (Chinese VAT for services); set
    # via server-side default at migration time so existing rows
    # auto-populate.
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)

    # Timestamps
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # group_id — R7 / Felix 2026-06-12 display grouping. FK to
    # v3_order_groups, ON DELETE SET NULL so deleting the group
    # unassigns its members rather than cascading. Migration 0015.
    group_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("v3_order_groups.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    @property
    def has_inquiry(self) -> bool:
        return self.inquiry_data is not None


class OrderGroup(Base):
    """User-defined display grouping for a set of Orders (R7).

    Felix 2026-06-12: "三个订单是同一天一条船的，能打勾合到一天" —
    purely a visual aggregation. Each Order keeps independent identity,
    inquiry pipeline, and fulfillment lifecycle. Group exists only to
    fold the list page into a collapsible tree.

    Owned by `user_id` (RBAC; only the owner sees / mutates the group).
    `ship_name` + `loading_date` are denormalized from the typical
    grouping criterion (same ship + same date) so the list page can
    filter / sort without joining order rows.
    """

    __tablename__ = "v3_order_groups"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    ship_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    loading_date: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
