"""Add deterministic workbench upload audit metadata.

Revision ID: 0028_upload_workbench_audit
Revises: 0027_product_price_periods
"""

import sqlalchemy as sa
from alembic import op

revision = "0028_upload_workbench_audit"
down_revision = "0027_product_price_periods"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("v3_upload_batches", sa.Column("file_sha256", sa.String(length=64), nullable=True))
    op.add_column("v3_upload_batches", sa.Column("sheet_name", sa.String(length=200), nullable=True))
    op.add_column("v3_upload_batches", sa.Column("header_row_number", sa.Integer(), nullable=True))
    op.add_column("v3_upload_batches", sa.Column("header_diagnostics", sa.JSON(), nullable=True))
    op.add_column(
        "v3_upload_batches",
        sa.Column("workflow_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column("v3_staging_products", sa.Column("source_row_number", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("v3_staging_products", "source_row_number")
    for name in (
        "workflow_version",
        "header_diagnostics",
        "header_row_number",
        "sheet_name",
        "file_sha256",
    ):
        op.drop_column("v3_upload_batches", name)
