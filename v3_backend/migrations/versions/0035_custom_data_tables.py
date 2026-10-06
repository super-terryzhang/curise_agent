"""Install custom data storage without modifying existing business tables.

Revision ID: 0035_custom_data_tables
Revises: 0034_drop_product_validity
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035_custom_data_tables"
down_revision = "0034_drop_product_validity"
branch_labels = None
depends_on = None


def identity():
    return sa.Column("id", sa.Uuid(), primary_key=True)


def timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    op.create_table(
        "v3_data_tables",
        identity(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("display_field_id", sa.Uuid()),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=False),
        *timestamps(),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_data_table_status"),
        sa.CheckConstraint("schema_version >= 1", name="ck_data_table_version"),
    )
    op.create_table(
        "v3_data_fields",
        identity(),
        sa.Column(
            "table_id",
            sa.Uuid(),
            sa.ForeignKey("v3_data_tables.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("field_type", sa.String(20), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("unique", sa.Boolean(), nullable=False),
        sa.Column("default_value", json_type()),
        sa.Column("config", json_type(), nullable=False),
        sa.Column(
            "target_table_id", sa.Uuid(), sa.ForeignKey("v3_data_tables.id", ondelete="RESTRICT")
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("table_id", "id", name="uq_data_field_table_id"),
        sa.UniqueConstraint("table_id", "id", "target_table_id", name="uq_data_field_target"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_data_field_status"),
        sa.CheckConstraint("schema_version >= 1", name="ck_data_field_version"),
        sa.CheckConstraint("sort_order >= 0", name="ck_data_field_order"),
        sa.CheckConstraint(
            "field_type IN ('text','number','date','datetime','single_select','multi_select','boolean','link')",
            name="ck_data_field_type",
        ),
        sa.CheckConstraint(
            "(field_type = 'link' AND target_table_id IS NOT NULL) OR (field_type <> 'link' AND target_table_id IS NULL)",
            name="ck_data_field_link",
        ),
        sa.CheckConstraint(
            "NOT \"unique\" OR field_type IN ('text','number')", name="ck_data_field_unique_type"
        ),
    )
    # The display FK is circular, so install it only after both tables exist.
    with op.batch_alter_table("v3_data_tables") as batch:
        batch.create_foreign_key(
            "fk_data_table_display_field",
            "v3_data_fields",
            ["id", "display_field_id"],
            ["table_id", "id"],
            ondelete="RESTRICT",
        )
    op.create_table(
        "v3_data_records",
        identity(),
        sa.Column(
            "table_id",
            sa.Uuid(),
            sa.ForeignKey("v3_data_tables.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("values", json_type(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("table_id", "id", name="uq_data_record_table_id"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_data_record_status"),
        sa.CheckConstraint("revision >= 1 AND schema_version >= 1", name="ck_data_record_versions"),
    )
    op.create_table(
        "v3_data_links",
        sa.Column("table_id", sa.Uuid(), nullable=False),
        sa.Column("record_id", sa.Uuid(), primary_key=True),
        sa.Column("field_id", sa.Uuid(), primary_key=True),
        sa.Column("target_table_id", sa.Uuid(), nullable=False),
        sa.Column("target_record_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["table_id", "record_id"],
            ["v3_data_records.table_id", "v3_data_records.id"],
            name="fk_data_link_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["table_id", "field_id", "target_table_id"],
            ["v3_data_fields.table_id", "v3_data_fields.id", "v3_data_fields.target_table_id"],
            name="fk_data_link_field_target",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_table_id", "target_record_id"],
            ["v3_data_records.table_id", "v3_data_records.id"],
            name="fk_data_link_target",
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "v3_data_unique_values",
        sa.Column("table_id", sa.Uuid(), nullable=False),
        sa.Column("record_id", sa.Uuid(), primary_key=True),
        sa.Column("field_id", sa.Uuid(), primary_key=True),
        sa.Column("value", sa.String(200), nullable=False),
        sa.UniqueConstraint("field_id", "value", name="uq_data_unique_value"),
        sa.ForeignKeyConstraint(
            ["table_id", "record_id"],
            ["v3_data_records.table_id", "v3_data_records.id"],
            name="fk_data_unique_record",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["table_id", "field_id"],
            ["v3_data_fields.table_id", "v3_data_fields.id"],
            name="fk_data_unique_field",
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "v3_data_changes",
        identity(),
        sa.Column(
            "table_id",
            sa.Uuid(),
            sa.ForeignKey("v3_data_tables.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("field_id", sa.Uuid()),
        sa.Column("record_id", sa.Uuid()),
        sa.Column("entity_type", sa.String(10), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("before", json_type()),
        sa.Column("after", json_type()),
        sa.Column("display_snapshot", json_type(), nullable=False),
        sa.Column("creation_request", json_type()),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("actor_role", sa.String(30), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["table_id", "field_id"],
            ["v3_data_fields.table_id", "v3_data_fields.id"],
            name="fk_data_change_field",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["table_id", "record_id"],
            ["v3_data_records.table_id", "v3_data_records.id"],
            name="fk_data_change_record",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "entity_type IN ('table','field','record')", name="ck_data_change_entity"
        ),
        sa.CheckConstraint("schema_version >= 1", name="ck_data_change_version"),
    )
    op.create_index(
        "ix_data_fields_table_status", "v3_data_fields", ["table_id", "status", "sort_order"]
    )
    op.create_index(
        "ix_data_records_table_status", "v3_data_records", ["table_id", "status", "created_at"]
    )
    op.create_index(
        "ix_data_links_target", "v3_data_links", ["target_table_id", "target_record_id"]
    )
    op.create_index("ix_data_changes_history", "v3_data_changes", ["table_id", "created_at", "id"])


def downgrade():
    # Never invoke on production: it destroys custom user data.
    for name in ("v3_data_changes", "v3_data_unique_values", "v3_data_links", "v3_data_records"):
        op.drop_table(name)
    with op.batch_alter_table("v3_data_tables") as batch:
        batch.drop_constraint("fk_data_table_display_field", type_="foreignkey")
    op.drop_table("v3_data_fields")
    op.drop_table("v3_data_tables")
