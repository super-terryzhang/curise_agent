"""Order loading_date — date the supplies are loaded onto the cruise ship.

Revision ID: 0013_order_loading_date
Revises: 0012_order_cost_items
Create Date: 2026-06-16

Distinct from `delivery_date` (when supplier ships to the port). Per
Felix 2026-06-12 request: surface loading_date on the order list page
so finance / ops can see ship loading schedule at a glance.

Stored as `String(50)` to match the existing `delivery_date` column's
convention — raw value preserved from the document. Tech debt: both
should eventually be `Date` for sortable / groupable semantics
(see docs/current_progress/2026-06-16/tech_debt_log.md TD-3).

No backfill: existing orders keep NULL. New PO extractions populate
this from the Gemini extraction schema (`order_metadata.loading_date`).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_order_loading_date"
down_revision: str | Sequence[str] | None = "0012_order_cost_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "v2_orders",
        sa.Column("loading_date", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("v2_orders", "loading_date")
