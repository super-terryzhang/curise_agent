"""orders expand — add PO-specific columns (ADR-0001)

Revision ID: 0002_orders_expand
Revises: 0001_baseline
Create Date: 2026-04-24

Phase 3 migration. Expand-only: adds 7 new columns to the existing
`v2_orders` table without touching the legacy JSON columns
(`extraction_data`, `order_metadata`, `products`).

After this migration:
- v2 code continues to write to `extraction_data.metadata.po_number` etc.
- v3 code reads from and writes to the new columns
- During the double-write window, `scripts/backfill_order_fields.py`
  copies historical JSON values into the new columns
- The contract migration (drop of legacy JSON) is Phase 7+, NOT here

Rollback: drop the 7 new columns. No data loss — the JSON fields still
hold the canonical values during the transition.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_orders_expand"
down_revision: str | Sequence[str] | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_NEW_COLUMNS: list[tuple[str, sa.types.TypeEngine]] = [
    ("po_number", sa.String(100)),
    ("ship_name", sa.String(200)),
    ("vendor_name", sa.String(200)),
    ("order_date", sa.String(50)),
    ("currency", sa.String(20)),
    ("destination_port", sa.String(200)),
    ("field_evidence", sa.JSON()),
]


def upgrade() -> None:
    with op.batch_alter_table("v2_orders") as batch:
        for name, column_type in _NEW_COLUMNS:
            batch.add_column(sa.Column(name, column_type, nullable=True))
    # Index the PO number for fast lookup by PO (frequent ops query)
    op.create_index(
        "ix_v2_orders_po_number",
        "v2_orders",
        ["po_number"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_v2_orders_po_number", table_name="v2_orders")
    with op.batch_alter_table("v2_orders") as batch:
        for name, _ in _NEW_COLUMNS:
            batch.drop_column(name)
