"""Track single Oracle PO imports without changing historical orders."""

import sqlalchemy as sa
from alembic import op

revision = "0026_oracle_po_imports"
down_revision = "0025_product_price_history"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "v3_oracle_po_imports",
        sa.Column("source_key", sa.String(64), primary_key=True),
        sa.Column("version_key", sa.String(64), nullable=False),
        sa.Column("po_number", sa.String(100), nullable=False),
        sa.Column("source_record", sa.JSON(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("pdf_sha256", sa.String(64)),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("v2_documents.id"), unique=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("v2_orders.id"), unique=True),
        sa.Column("inquiry_id", sa.Integer(), sa.ForeignKey("v3_inquiries.id")),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("stage", sa.String(30), nullable=False),
        sa.Column("issues", sa.JSON()),
        sa.Column("error_code", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_v3_oracle_po_imports_po_number", "v3_oracle_po_imports", ["po_number"])

    op.create_table(
        "v3_oracle_scan_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("active_key", sa.String(20), unique=True),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("v3_oracle_scan_runs")
    op.drop_index("ix_v3_oracle_po_imports_po_number", table_name="v3_oracle_po_imports")
    op.drop_table("v3_oracle_po_imports")
