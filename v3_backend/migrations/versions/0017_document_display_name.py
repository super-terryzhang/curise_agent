"""Document.display_name — user-facing rename (P3 2026-06-21).

Revision ID: 0017_document_display_name
Revises: 0016_normalize_role_aliases
Create Date: 2026-06-21

Users complained that documents always show the raw upload filename
(`PO_20260619_RoyalCaribbean_Sydney.xls`), with no way to give them a
human label like "Sydney 2026-06 PO". `filename` itself stays immutable
because (a) it's part of how we look up the blob in storage and (b)
audit trails care about the original. `display_name` is a separate
optional column the frontend prefers when present.

Backfill: not needed. NULL renders as `filename` in the UI fallback
`display_name ?? filename`, so existing rows look unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_document_display_name"
down_revision: str | Sequence[str] | None = "0016_normalize_role_aliases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "v2_documents",
        sa.Column("display_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("v2_documents", "display_name")
