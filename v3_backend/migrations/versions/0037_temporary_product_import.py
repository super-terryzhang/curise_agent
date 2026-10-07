"""Add the isolated temporary product import foundation.

Revision ID: 0037_temporary_product_import
Revises: 0036_unified_data_tables
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0037_temporary_product_import"
down_revision = "0036_unified_data_tables"
branch_labels = None
depends_on = None


def json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    op.add_column(
        "v3_product_price_periods",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index(
        "uq_products_active_normalized_code_port",
        "products",
        [sa.text("lower(btrim(code))"), "port_id"],
        unique=True,
        postgresql_where=sa.text("status AND code IS NOT NULL AND port_id IS NOT NULL"),
    )
    op.create_table(
        "v3_import_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("file_url", sa.String(500)),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("target_table", sa.String(30), nullable=False),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column("product_schema_version", sa.Integer(), nullable=False),
        sa.Column("field_manifest", json_type(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("create_count", sa.Integer(), nullable=False),
        sa.Column("update_count", sa.Integer(), nullable=False),
        sa.Column("skip_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("block_count", sa.Integer(), nullable=False),
        sa.Column("result", json_type()),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('uploaded','validating','ready','committed','failed','cancelled','rolled_back')",
            name="ck_import_batch_status",
        ),
        sa.CheckConstraint("contract_version >= 1", name="ck_import_batch_contract_version"),
        sa.CheckConstraint(
            "product_schema_version >= 1", name="ck_import_batch_product_schema_version"
        ),
        sa.CheckConstraint("length(file_sha256) = 64", name="ck_import_batch_file_sha256"),
    )
    op.create_index(
        "ix_import_batches_user_created", "v3_import_batches", ["user_id", "created_at"]
    )
    op.create_table(
        "v3_import_rows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "batch_id", sa.Uuid(), sa.ForeignKey("v3_import_batches.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("sheet_key", sa.String(20), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("raw_values", json_type(), nullable=False),
        sa.Column("normalized_values", json_type(), nullable=False),
        sa.Column("product_code_normalized", sa.String(100)),
        sa.Column("port_id", sa.Integer()),
        sa.Column("target_product_id", sa.Integer()),
        sa.Column("target_period_id", sa.Integer()),
        sa.Column("snapshot", json_type(), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("issues", json_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "batch_id", "sheet_key", "source_row_number", name="uq_import_row_source"
        ),
        sa.CheckConstraint("sheet_key IN ('products','prices')", name="ck_import_row_sheet"),
        sa.CheckConstraint("source_row_number >= 2", name="ck_import_row_source_number"),
        sa.CheckConstraint(
            "action IN ('pending','create','update','skip','warning','block')",
            name="ck_import_row_action",
        ),
    )
    op.create_index(
        "ix_import_rows_batch_action", "v3_import_rows", ["batch_id", "action", "source_row_number"]
    )
    op.create_table(
        "v3_import_changes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "batch_id", sa.Uuid(), sa.ForeignKey("v3_import_batches.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.String(100), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("before", json_type()),
        sa.Column("after", json_type()),
        sa.Column("expected_after_revision", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("batch_id", "sequence", name="uq_import_change_sequence"),
        sa.CheckConstraint(
            "entity_type IN ('product','extension','price_period')", name="ck_import_change_entity_type"
        ),
        sa.CheckConstraint(
            "action IN ('create','update','archive')", name="ck_import_change_action"
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_import_change_sequence"),
    )
    op.create_index(
        "ix_import_changes_batch_entity", "v3_import_changes", ["batch_id", "entity_type", "entity_id"]
    )


def downgrade():
    op.drop_index("ix_import_changes_batch_entity", table_name="v3_import_changes")
    op.drop_table("v3_import_changes")
    op.drop_index("ix_import_rows_batch_action", table_name="v3_import_rows")
    op.drop_table("v3_import_rows")
    op.drop_index("ix_import_batches_user_created", table_name="v3_import_batches")
    op.drop_table("v3_import_batches")
    op.drop_index("uq_products_active_normalized_code_port", table_name="products")
    op.drop_column("v3_product_price_periods", "revision")

