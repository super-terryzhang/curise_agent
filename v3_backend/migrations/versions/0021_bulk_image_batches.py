"""Bulk image upload batches — ZIP-based product image ingestion (2026-06-22).

Revision ID: 0021_bulk_image_batches
Revises: 0020_user_capabilities
Create Date: 2026-06-22

Why these two tables:
    Felix needs to upload product images in batches of hundreds at a
    time. Doing it one-product-at-a-time via the existing gallery would
    require N popups for N products. The ZIP flow uploads a single
    archive with `<country>/<port>/<product_code>/` directory tree;
    backend matches each non-empty folder to a Product row and ingests
    the images.

    To make the workflow safe (no surprise mass-mutation), we follow the
    proven `data_upload` pattern from masterdata: a STAGING phase that
    parses + matches without writing to live `v3_product_images`, then a
    PREVIEW (user reviews matches), then a COMMIT (background job runs
    real ingestion via the existing `add_product_image` service).

    Two tables — `v3_bulk_image_batches` for the batch envelope (status
    state machine + counters) and `v3_bulk_image_staging` for per-row
    parsed entries (one row per image file inside the ZIP).

Lifecycle:
    uploading -> preview_ready -> processing -> completed
                              \> cancelled
                              \> error (parse/validation failed)

    Status is the lock that prevents double-commit (R3 risk in plan).

Why ZIP is uploaded to GCS, not stored as BLOB in DB:
    A 100MB ZIP would bloat the Postgres row. The staging table only
    holds the storage_key for retrieval; the actual ZIP lives at
    `bulk-image-staging/{batch_id}.zip` and is deleted after commit (or
    after 24h via the cleanup hook).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_bulk_image_batches"
down_revision: str | Sequence[str] | None = "0020_user_capabilities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v3_bulk_image_batches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # User-visible filename of the uploaded ZIP. Stored for the
        # report UI ("Imported from `summer-batch.zip`") + audit trail.
        sa.Column("zip_filename", sa.String(length=500), nullable=False),
        # GCS key where the raw ZIP lives during preview. Cleared
        # (set NULL) after commit so cleanup can detect orphans.
        sa.Column("zip_storage_key", sa.String(length=500), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="uploading",
        ),
        # Counters populated during parse; let the UI render the
        # preview header without scanning the staging table.
        sa.Column("total_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unmatched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        # Committed-images counter — populated by the background job
        # so the polling UI can render progress.
        sa.Column("ingested_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )

    op.create_table(
        "v3_bulk_image_staging",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("v3_bulk_image_batches.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # Path inside the ZIP — verbatim, for error messages and audit.
        # Example: "USA/Yokohama/BEEF-001/photo1.jpg"
        sa.Column("zip_path", sa.String(length=1000), nullable=False),
        # Parsed identity (path segments). Stored even when the match
        # fails so the UI can show "couldn't find BEEF-001 in
        # USA/Yokohama".
        sa.Column("country_name", sa.String(length=100), nullable=True),
        sa.Column("port_name", sa.String(length=100), nullable=True),
        sa.Column("product_code", sa.String(length=100), nullable=True),
        sa.Column("image_filename", sa.String(length=255), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        # Resolved product_id when the three-tuple matched a row.
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # matched / unmatched / error / committed / committed_failed
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="matched",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Speeds up the preview UI's "list all entries for batch X grouped
    # by product" query pattern.
    op.create_index(
        "ix_bulk_image_staging_batch_product",
        "v3_bulk_image_staging",
        ["batch_id", "product_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bulk_image_staging_batch_product",
        table_name="v3_bulk_image_staging",
    )
    op.drop_table("v3_bulk_image_staging")
    op.drop_table("v3_bulk_image_batches")
