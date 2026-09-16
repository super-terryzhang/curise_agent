"""Document folder — add optional color (2026-07-22).

Revision ID: 0023_document_folder_color
Revises: 0022_bulk_image_batch_updated_at
Create Date: 2026-07-22

Why:
    Folders got auto-derived colors on the frontend (id % 12 palette lookup)
    for the sidebar dot + row chip. That's fine for a first pass but two
    unrelated folders can collide in appearance, and users have no way to
    say "make this Chase folder green" if the id-hash lands on red.

    `color` is nullable so old rows stay on the deterministic fallback
    without a backfill — the frontend already handles null via
    `folderColor()`. Store as a plain string (hex code or palette key);
    validation lives in the service layer so palette shifts don't need a
    migration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_document_folder_color"
down_revision: str | Sequence[str] | None = "0022_bulk_image_batch_updated_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "v3_document_folders",
        sa.Column("color", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("v3_document_folders", "color")
