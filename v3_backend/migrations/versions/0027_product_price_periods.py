"""Add compatibility dates plus multi-period product pricing.

Revision ID: 0027_product_price_periods
Revises: 0026_oracle_po_imports
"""

import sqlalchemy as sa
from alembic import op

revision = "0027_product_price_periods"
down_revision = "0026_oracle_po_imports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Compatibility projection for the already-built product form/workbench.
    # The child table below is the canonical multi-period source used by matching.
    for name in (
        "purchase_price_effective_from",
        "purchase_price_effective_to",
        "selling_price_effective_from",
        "selling_price_effective_to",
    ):
        op.add_column("products", sa.Column(name, sa.DateTime(), nullable=True))
    op.create_check_constraint(
        "ck_products_purchase_price_period",
        "products",
        "purchase_price_effective_from IS NULL OR "
        "purchase_price_effective_to IS NULL OR "
        "purchase_price_effective_from <= purchase_price_effective_to",
    )
    op.create_check_constraint(
        "ck_products_selling_price_period",
        "products",
        "selling_price_effective_from IS NULL OR "
        "selling_price_effective_to IS NULL OR "
        "selling_price_effective_from <= selling_price_effective_to",
    )
    op.create_table(
        "v3_product_price_periods",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("price_type", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(length=20), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=False),
        sa.Column("status", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("source", sa.String(length=20), server_default="internal", nullable=False),
        sa.Column("source_batch_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "price_type IN ('purchase', 'selling')",
            name="ck_product_price_period_type",
        ),
        sa.CheckConstraint(
            "amount >= 0 AND amount <= 99999999.99",
            name="ck_product_price_period_amount",
        ),
        sa.CheckConstraint(
            "effective_from <= effective_to",
            name="ck_product_price_period_dates",
        ),
        sa.UniqueConstraint(
            "product_id",
            "price_type",
            "effective_from",
            "effective_to",
            name="uq_product_price_period_bounds",
        ),
    )
    op.create_index(
        "ix_product_price_period_lookup",
        "v3_product_price_periods",
        ["product_id", "price_type", "status", "effective_from", "effective_to"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_price_period_lookup", table_name="v3_product_price_periods"
    )
    op.drop_table("v3_product_price_periods")
    op.drop_constraint("ck_products_selling_price_period", "products", type_="check")
    op.drop_constraint("ck_products_purchase_price_period", "products", type_="check")
    for name in (
        "selling_price_effective_to",
        "selling_price_effective_from",
        "purchase_price_effective_to",
        "purchase_price_effective_from",
    ):
        op.drop_column("products", name)
