"""inquiry — expand v2 inquiry_data JSON into relational tables (ADR-0004).

Revision ID: 0004_inquiry_relational
Revises: 0003_settings_inquiry
Create Date: 2026-04-26

Phase 5 migration. Creates `v3_inquiries` and `v3_inquiry_suppliers` —
both new (no v2 equivalent). The legacy `Order.inquiry_data` JSON column
stays in place during the cutover window; v3 reads/writes the new tables
and serializes to v2-shaped JSON for the frontend.

Rollback: drop the two new tables. No data loss — `Order.inquiry_data`
still holds the canonical state during the transition.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_inquiry_relational"
down_revision: str | Sequence[str] | None = "0003_settings_inquiry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v3_inquiries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "order_id",
            sa.Integer,
            sa.ForeignKey("v2_orders.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime, nullable=True),
        sa.Column("supplier_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("unassigned_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_elapsed_seconds", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_v3_inquiries_order_id", "v3_inquiries", ["order_id"])

    op.create_table(
        "v3_inquiry_suppliers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "inquiry_id",
            sa.Integer,
            sa.ForeignKey("v3_inquiries.id"),
            nullable=False,
        ),
        sa.Column("supplier_id", sa.Integer, nullable=False),
        sa.Column("supplier_name", sa.String(200), nullable=True),
        sa.Column("supplier_info", sa.JSON, nullable=True),
        sa.Column("product_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("subtotal", sa.Float, nullable=True),
        sa.Column("currency", sa.String(20), nullable=True),
        sa.Column("template_id", sa.Integer, nullable=True),
        sa.Column("template_name", sa.String(200), nullable=True),
        sa.Column("template_selection_method", sa.String(40), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("excel_file_url", sa.String(500), nullable=True),
        sa.Column("preview_html_url", sa.String(500), nullable=True),
        sa.Column("verify_results", sa.JSON, nullable=True),
        sa.Column("missing_fields", sa.JSON, nullable=True),
        sa.Column("elapsed_seconds", sa.Float, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index(
        "ix_v3_inquiry_suppliers_inquiry_id",
        "v3_inquiry_suppliers",
        ["inquiry_id"],
    )
    op.create_index(
        "ix_v3_inquiry_suppliers_supplier_id",
        "v3_inquiry_suppliers",
        ["supplier_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_v3_inquiry_suppliers_supplier_id", table_name="v3_inquiry_suppliers")
    op.drop_index("ix_v3_inquiry_suppliers_inquiry_id", table_name="v3_inquiry_suppliers")
    op.drop_table("v3_inquiry_suppliers")
    op.drop_index("ix_v3_inquiries_order_id", table_name="v3_inquiries")
    op.drop_table("v3_inquiries")
