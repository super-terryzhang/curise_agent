"""Add reusable, audited product unit-conversion rules.

Revision ID: 0031_unit_conversion_rules
Revises: 0030_direct_bulk_images
Create Date: 2026-09-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_unit_conversion_rules"
down_revision: str | Sequence[str] | None = "0030_direct_bulk_images"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v3_unit_conversion_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("scope_type", sa.String(length=20), nullable=False),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("source_system", sa.String(length=30), nullable=False),
        sa.Column("source_unit", sa.String(length=50), nullable=False),
        sa.Column("target_unit", sa.String(length=50), nullable=False),
        sa.Column("source_quantity", sa.Numeric(20, 10), nullable=False),
        sa.Column("target_quantity", sa.Numeric(20, 10), nullable=False),
        sa.Column("target_step", sa.Numeric(20, 10), nullable=True),
        sa.Column("break_pack", sa.Boolean(), nullable=True),
        sa.Column("pack_signature", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("verified_by", sa.Integer(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "scope_type IN ('source_unit', 'product')",
            name="ck_unit_conversion_scope_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'verified', 'retired')",
            name="ck_unit_conversion_status",
        ),
        sa.CheckConstraint(
            "source_quantity > 0 AND target_quantity > 0",
            name="ck_unit_conversion_positive_quantities",
        ),
        sa.CheckConstraint(
            "target_step IS NULL OR target_step > 0",
            name="ck_unit_conversion_positive_target_step",
        ),
        sa.CheckConstraint("revision > 0", name="ck_unit_conversion_positive_revision"),
        sa.CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to",
            name="ck_unit_conversion_valid_period",
        ),
        sa.CheckConstraint(
            "(scope_type = 'source_unit' AND product_id IS NULL "
            "AND pack_signature IS NULL) OR "
            "(scope_type = 'product' AND product_id IS NOT NULL "
            "AND pack_signature IS NOT NULL)",
            name="ck_unit_conversion_scope_product",
        ),
        sa.CheckConstraint(
            "status != 'verified' OR (verified_by IS NOT NULL AND verified_at IS NOT NULL)",
            name="ck_unit_conversion_verified_metadata",
        ),
        sa.CheckConstraint(
            "length(trim(evidence)) > 0",
            name="ck_unit_conversion_evidence",
        ),
    )
    op.create_index(
        "uq_unit_conversion_verified_source",
        "v3_unit_conversion_rules",
        ["source_system", "source_unit", "target_unit"],
        unique=True,
        postgresql_where=sa.text("status = 'verified' AND product_id IS NULL"),
        sqlite_where=sa.text("status = 'verified' AND product_id IS NULL"),
    )
    op.create_index(
        "uq_unit_conversion_verified_product",
        "v3_unit_conversion_rules",
        ["product_id", "source_system", "source_unit", "target_unit", "pack_signature"],
        unique=True,
        postgresql_where=sa.text("status = 'verified' AND product_id IS NOT NULL"),
        sqlite_where=sa.text("status = 'verified' AND product_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_unit_conversion_verified_product", table_name="v3_unit_conversion_rules"
    )
    op.drop_index(
        "uq_unit_conversion_verified_source", table_name="v3_unit_conversion_rules"
    )
    op.drop_table("v3_unit_conversion_rules")
