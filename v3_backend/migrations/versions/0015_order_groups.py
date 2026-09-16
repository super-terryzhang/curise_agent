"""Order display grouping — `v3_order_groups` table + `v2_orders.group_id`.

Revision ID: 0015_order_groups
Revises: 0014_product_contract_price
Create Date: 2026-06-16

Per Felix 2026-06-12 request: surface a "合单" workflow that lets users
collapse multiple Order rows (typically same ship + same loading date)
under a user-named group. Display-level only — Orders remain distinct
entities; group is just a visual / catalog tag. Downstream (inquiry,
fulfillment, Excel) continues to operate on individual Order rows.

Schema:
  - New `v3_order_groups` table: id, user_id (owner, for RBAC),
    name (user-editable), ship_name + loading_date (denormalized for
    list filters), timestamps.
  - New `group_id` column on `v2_orders` — FK to v3_order_groups with
    `ON DELETE SET NULL` so deleting a group unassigns its orders
    rather than cascading.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_order_groups"
down_revision: str | Sequence[str] | None = "0014_product_contract_price"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v3_order_groups",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("ship_name", sa.String(200), nullable=True),
        sa.Column("loading_date", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )

    op.add_column(
        "v2_orders",
        sa.Column(
            "group_id",
            sa.Integer,
            sa.ForeignKey("v3_order_groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_v2_orders_group_id",
        "v2_orders",
        ["group_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_v2_orders_group_id", table_name="v2_orders")
    op.drop_column("v2_orders", "group_id")
    op.drop_table("v3_order_groups")
