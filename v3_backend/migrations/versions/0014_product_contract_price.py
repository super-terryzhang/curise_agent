"""Product `contract_price` — the binding contract selling price.

Revision ID: 0014_product_contract_price
Revises: 0013_order_loading_date
Create Date: 2026-06-16

Per Felix 2026-06-06 request: cruise customer POs sometimes carry the
wrong unit_price (typos / outdated price lists). We need to store our
side's contracted selling price separately from the existing
`Product.price` (which is treated as a default list price and may drift
over time). The matcher will surface `contract_price` next to the PO's
`unit_price` so finance can spot mismatches.

- New column `contract_price` on `products` (Numeric(10, 2), NULL-able)
- No backfill: existing rows keep NULL. Filled per-product via Excel
  upload pipeline + masterdata CRUD as part of the rollout.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_product_contract_price"
down_revision: str | Sequence[str] | None = "0013_order_loading_date"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("contract_price", sa.Numeric(10, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("products", "contract_price")
