"""Product images table (R5 2026-06-22).

Revision ID: 0019_product_images
Revises: 0018_document_folders
Create Date: 2026-06-22

R5 deliverable: every Product can have N images. Storing as a separate
table (not JSONB on Product) because:
    - We need per-image metadata (alt_text, display_order, uploader)
    - JSONB array of attachments is the canonical anti-pattern for many-
      to-one media (bloat, lock contention, hard to index `display_order`)
    - Standard ecommerce schema (Shopify, Magento, WooCommerce all use a
      separate product_images table)

Storage strategy: three keys per image (original / 400px medium / 80px
thumbnail), all generated at upload time by `domains.masterdata.images
.thumbnails.generate_thumbnails`. We store the storage keys (opaque)
rather than URLs because:
    1. URLs are signed and expire (1h) — can't persist them
    2. Switching storage backends (GCS / Supabase / S3) shouldn't require
       a data migration; the abstraction in `infrastructure.storage`
       hides that

display_order convention:
    0 = primary image (the one shown in list-page thumbnails). No
    `is_primary` boolean because that would let us drift into a state
    where two rows have is_primary=True or zero have it. One source of
    truth wins.

CASCADE on delete: removing a Product cleans up its image rows. Blob
cleanup in object storage is best-effort and handled by the service
layer (a nightly script picks up any orphans later).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_product_images"
down_revision: str | Sequence[str] | None = "0018_document_folders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v3_product_images",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # Three storage keys per image — the FileStorage abstraction (GCS in
        # prod, Local in dev, Supabase legacy) treats them as opaque strings.
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("thumbnail_key", sa.String(length=500), nullable=False),
        sa.Column("medium_key", sa.String(length=500), nullable=False),
        # Audit + display
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=30), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        # Ordering & metadata
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alt_text", sa.String(length=500), nullable=True),
        # Audit
        sa.Column("uploaded_by_user_id", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
    )
    # Index: (product_id, display_order) speeds up the "list images for a
    # product, sorted by display_order" query pattern that powers both the
    # detail-page gallery and the list-page thumbnail projection.
    op.create_index(
        "ix_product_images_pid_order",
        "v3_product_images",
        ["product_id", "display_order"],
    )


def downgrade() -> None:
    op.drop_index("ix_product_images_pid_order", table_name="v3_product_images")
    op.drop_table("v3_product_images")
