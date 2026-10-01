"""Drop obsolete product-level validity columns.

Revision ID: 0034_drop_product_validity
Revises: 0033_product_validity_backfill
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_drop_product_validity"
down_revision: str | Sequence[str] | None = "0033_product_validity_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("products", "effective_to")
    op.drop_column("products", "effective_from")


def downgrade() -> None:
    op.add_column(
        "products",
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE products AS p
        SET effective_from = a.legacy_from,
            effective_to = a.legacy_to
        FROM v3_product_validity_migration_audit AS a
        WHERE a.product_id = p.id
        """
    )
