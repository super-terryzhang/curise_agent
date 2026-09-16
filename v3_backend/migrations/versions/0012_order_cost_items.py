"""Order cost items + per-order financial settings (display currency + tax rate).

Revision ID: 0012_order_cost_items
Revises: 0011_chat_session_model
Create Date: 2026-05-29

Two changes that together replace the v2 `financial_data` JSON snapshot
with a live, editable P&L model:

  1. New table `v3_order_cost_items` — one row per user-entered cost
     (freight, customs, insurance, labor, etc.). Multi-currency: each
     row stores its native amount + ISO currency code; conversion to
     the order's display currency happens on read via `v2_exchange_rates`.

  2. Two new columns on `v2_orders`:
       - `display_currency`  : preferred currency for P&L view (NULL =
                               fall back to the order's PO currency)
       - `tax_rate`          : per-order overridable tax rate, default
                               0.06 (Chinese VAT for services). Set
                               server-side default so existing rows
                               auto-populate.

`v2_orders.financial_data` (the old snapshot JSON column) is NOT
dropped in this migration — backward-readable as legacy notice during
the rollout. A follow-up migration removes it once the new tab has
been live for a release cycle.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_order_cost_items"
down_revision: str | Sequence[str] | None = "0011_chat_session_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v3_order_cost_items",
        sa.Column(
            "id",
            sa.Integer,
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "order_id",
            sa.Integer,
            sa.ForeignKey("v2_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
        sa.Column("created_by_user_id", sa.Integer, nullable=True),
    )
    op.create_index(
        "ix_v3_order_cost_items_order_id",
        "v3_order_cost_items",
        ["order_id"],
    )

    op.add_column(
        "v2_orders",
        sa.Column("display_currency", sa.String(10), nullable=True),
    )
    # server_default seeds existing rows (NULL columns can't have a
    # default that auto-fills, but server_default does for new + old).
    # Numeric(5,4) holds 0.0000–9.9999 — covers any realistic tax rate.
    op.add_column(
        "v2_orders",
        sa.Column(
            "tax_rate",
            sa.Numeric(5, 4),
            nullable=True,
            server_default=sa.text("0.06"),
        ),
    )


def downgrade() -> None:
    op.drop_column("v2_orders", "tax_rate")
    op.drop_column("v2_orders", "display_currency")
    op.drop_index(
        "ix_v3_order_cost_items_order_id",
        table_name="v3_order_cost_items",
    )
    op.drop_table("v3_order_cost_items")
