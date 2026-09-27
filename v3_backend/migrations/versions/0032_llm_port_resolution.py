"""Add auditable LLM port-resolution state to orders.

Revision ID: 0032_llm_port_resolution
Revises: 0031_unit_conversion_rules
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_llm_port_resolution"
down_revision: str | Sequence[str] | None = "0031_unit_conversion_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "v2_orders",
        sa.Column("port_resolution_method", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "v2_orders",
        sa.Column("port_resolution_status", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "v2_orders",
        sa.Column("port_resolution_data", sa.JSON(), nullable=True),
    )
    op.add_column(
        "v2_orders",
        sa.Column("port_resolution_reviewed_by", sa.Integer(), nullable=True),
    )
    op.add_column(
        "v2_orders",
        sa.Column(
            "port_resolution_reviewed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_v2_orders_port_resolution_method",
        "v2_orders",
        "port_resolution_method IS NULL OR port_resolution_method IN ('llm', 'manual')",
    )
    op.create_check_constraint(
        "ck_v2_orders_port_resolution_status",
        "v2_orders",
        "port_resolution_status IS NULL OR "
        "port_resolution_status IN ('pending_review', 'confirmed', 'overridden', 'unresolved')",
    )
    op.create_foreign_key(
        "fk_v2_orders_port_resolution_reviewed_by_users",
        "v2_orders",
        "users",
        ["port_resolution_reviewed_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_v2_orders_port_resolution_reviewed_by_users",
        "v2_orders",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_v2_orders_port_resolution_status",
        "v2_orders",
        type_="check",
    )
    op.drop_constraint(
        "ck_v2_orders_port_resolution_method",
        "v2_orders",
        type_="check",
    )
    op.drop_column("v2_orders", "port_resolution_reviewed_at")
    op.drop_column("v2_orders", "port_resolution_reviewed_by")
    op.drop_column("v2_orders", "port_resolution_data")
    op.drop_column("v2_orders", "port_resolution_status")
    op.drop_column("v2_orders", "port_resolution_method")
