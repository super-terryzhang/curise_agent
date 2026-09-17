"""Direct product-image workbench staging and order plans.

Revision ID: 0030_direct_bulk_images
Revises: 0029_arrangement_inquiries
Create Date: 2026-09-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_direct_bulk_images"
down_revision: str | Sequence[str] | None = "0029_arrangement_inquiries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "v3_bulk_image_batches",
        sa.Column("source_type", sa.String(length=20), nullable=False, server_default="zip"),
    )
    op.add_column(
        "v3_bulk_image_batches",
        sa.Column("excluded_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "v3_bulk_image_batches",
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
    )

    for column in (
        sa.Column("storage_key", sa.String(length=500), nullable=True),
        sa.Column("preview_storage_key", sa.String(length=500), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("issue_code", sa.String(length=50), nullable=True),
        sa.Column("upload_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("decision", sa.String(length=20), nullable=False, server_default="include"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("committed_image_id", sa.Integer(), nullable=True),
    ):
        op.add_column("v3_bulk_image_staging", column)
    op.create_foreign_key(
        "fk_bulk_image_staging_committed_image",
        "v3_bulk_image_staging",
        "v3_product_images",
        ["committed_image_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "v3_bulk_image_product_plans",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("v3_bulk_image_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expected_existing_image_ids", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("ordered_items", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("batch_id", "product_id", name="uq_bulk_image_plan_batch_product"),
    )
    op.create_index(
        "ix_bulk_image_product_plans_batch_id",
        "v3_bulk_image_product_plans",
        ["batch_id"],
    )
    op.create_index(
        "ix_bulk_image_product_plans_product_id",
        "v3_bulk_image_product_plans",
        ["product_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_bulk_image_product_plans_product_id", table_name="v3_bulk_image_product_plans")
    op.drop_index("ix_bulk_image_product_plans_batch_id", table_name="v3_bulk_image_product_plans")
    op.drop_table("v3_bulk_image_product_plans")
    op.drop_constraint(
        "fk_bulk_image_staging_committed_image",
        "v3_bulk_image_staging",
        type_="foreignkey",
    )
    for column in (
        "committed_image_id",
        "retry_count",
        "decision",
        "upload_order",
        "issue_code",
        "content_type",
        "preview_storage_key",
        "storage_key",
    ):
        op.drop_column("v3_bulk_image_staging", column)
    op.drop_column("v3_bulk_image_batches", "failed_count")
    op.drop_column("v3_bulk_image_batches", "excluded_count")
    op.drop_column("v3_bulk_image_batches", "source_type")
