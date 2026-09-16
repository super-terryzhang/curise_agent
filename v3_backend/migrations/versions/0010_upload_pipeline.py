"""Master-data upload pipeline — `v3_upload_batches`, `v3_staging_products`,
`v3_product_changelog`.

Revision ID: 0010_upload_pipeline
Revises: 0009_pending_actions
Create Date: 2026-05-14

These three tables back the chat-driven product upload flow
(`POST /api/data-upload/upload` + the `parse_uploaded_file` /
`preview_upload` / `commit_upload_batch` tools). They've existed in
`domains/masterdata/upload/models.py` since the upload pipeline was
introduced, but were never given an Alembic migration — only
`Base.metadata.create_all()` (tests) was creating them. Production using
Alembic-only never had them, so the first real user upload after the
template feature went live failed with:

    relation "v3_upload_batches" does not exist

This migration backfills the schema. Mirrors `0009_pending_actions`
shape (a similar table-was-defined-but-never-migrated case).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_upload_pipeline"
down_revision: str | Sequence[str] | None = "0009_pending_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── v3_upload_batches ─────────────────────────────────────
    op.create_table(
        "v3_upload_batches",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("file_url", sa.String(500), nullable=True),
        sa.Column(
            "entity_type",
            sa.String(30),
            nullable=False,
            server_default="products",
        ),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="parsing"
        ),
        sa.Column("total_rows", sa.Integer, nullable=False, server_default="0"),
        sa.Column("parsed_rows", sa.Integer, nullable=False, server_default="0"),
        sa.Column("matched_exact", sa.Integer, nullable=False, server_default="0"),
        sa.Column("matched_fuzzy", sa.Integer, nullable=False, server_default="0"),
        sa.Column("new_rows", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_rows", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("parsed_at", sa.DateTime, nullable=True),
        sa.Column("committed_at", sa.DateTime, nullable=True),
    )
    op.create_index(
        "ix_v3_upload_batches_user_id", "v3_upload_batches", ["user_id"]
    )

    # ─── v3_staging_products ───────────────────────────────────
    op.create_table(
        "v3_staging_products",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "batch_id",
            sa.Integer,
            sa.ForeignKey("v3_upload_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_index", sa.Integer, nullable=False),
        sa.Column("raw_data", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("product_code", sa.String(100), nullable=True),
        sa.Column("product_name", sa.String(500), nullable=True),
        sa.Column("supplier_code", sa.String(100), nullable=True),
        sa.Column("price", sa.Float, nullable=True),
        sa.Column("unit", sa.String(50), nullable=True),
        sa.Column("pack_size", sa.String(100), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "match_status",
            sa.String(20),
            nullable=False,
            server_default="unresolved",
        ),
        sa.Column("match_target_id", sa.Integer, nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("validation_errors", sa.JSON, nullable=True),
    )
    op.create_index(
        "ix_v3_staging_products_batch_id",
        "v3_staging_products",
        ["batch_id"],
    )
    op.create_index(
        "ix_v3_staging_products_product_code",
        "v3_staging_products",
        ["product_code"],
    )

    # ─── v3_product_changelog ──────────────────────────────────
    op.create_table(
        "v3_product_changelog",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "batch_id",
            sa.Integer,
            sa.ForeignKey("v3_upload_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("product_id", sa.Integer, nullable=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("field_name", sa.String(100), nullable=True),
        sa.Column("old_value", sa.Text, nullable=True),
        sa.Column("new_value", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )
    op.create_index(
        "ix_v3_product_changelog_batch_id",
        "v3_product_changelog",
        ["batch_id"],
    )
    op.create_index(
        "ix_v3_product_changelog_product_id",
        "v3_product_changelog",
        ["product_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_v3_product_changelog_product_id", table_name="v3_product_changelog"
    )
    op.drop_index(
        "ix_v3_product_changelog_batch_id", table_name="v3_product_changelog"
    )
    op.drop_table("v3_product_changelog")

    op.drop_index(
        "ix_v3_staging_products_product_code", table_name="v3_staging_products"
    )
    op.drop_index(
        "ix_v3_staging_products_batch_id", table_name="v3_staging_products"
    )
    op.drop_table("v3_staging_products")

    op.drop_index(
        "ix_v3_upload_batches_user_id", table_name="v3_upload_batches"
    )
    op.drop_table("v3_upload_batches")
