"""Unify system and user-created data tables without copying core rows.

Revision ID: 0036_unified_data_tables
Revises: 0035_custom_data_tables
"""

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision = "0036_unified_data_tables"
down_revision = "0035_custom_data_tables"
branch_labels = None
depends_on = None


SYSTEM_TABLES = (
    (UUID("025588dd-ae63-5607-9e78-1179a500ed6e"), "products", "产品"),
    (UUID("480cc5e5-5882-58d1-a227-e8ee734c866f"), "suppliers", "供应商"),
    (UUID("0bc67ecc-ccd3-53b5-8327-74f4b482b7b4"), "orders", "订单"),
)


def upgrade():
    with op.batch_alter_table("v3_data_tables") as batch:
        batch.add_column(
            sa.Column(
                "table_kind",
                sa.String(10),
                nullable=False,
                server_default=sa.text("'user'"),
            )
        )
        batch.add_column(sa.Column("system_key", sa.String(30), nullable=True))
        batch.create_check_constraint("ck_data_table_kind", "table_kind IN ('user', 'system')")
        batch.create_check_constraint(
            "ck_data_table_system_key",
            "(table_kind = 'system' AND system_key IS NOT NULL) OR "
            "(table_kind = 'user' AND system_key IS NULL)",
        )
        batch.create_unique_constraint("uq_data_table_system_key", ["system_key"])

    with op.batch_alter_table("v3_data_records") as batch:
        batch.add_column(sa.Column("source_record_id", sa.String(100), nullable=True))
        batch.create_check_constraint(
            "ck_data_record_source_record_id",
            "source_record_id IS NULL OR source_record_id <> ''",
        )
        batch.create_unique_constraint(
            "uq_data_record_source_record_id", ["table_id", "source_record_id"]
        )

    tables = sa.table(
        "v3_data_tables",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("table_kind", sa.String()),
        sa.column("system_key", sa.String()),
        sa.column("status", sa.String()),
        sa.column("schema_version", sa.Integer()),
        sa.column("display_field_id", sa.Uuid()),
        sa.column("created_by", sa.Integer()),
        sa.column("updated_by", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    bind = op.get_bind()
    now = datetime.now(UTC)
    for table_id, key, name in SYSTEM_TABLES:
        existing = bind.execute(
            sa.select(tables.c.id).where(tables.c.system_key == key)
        ).scalar_one_or_none()
        if existing is None:
            bind.execute(
                tables.insert().values(
                    id=table_id,
                    name=name,
                    description=None,
                    table_kind="system",
                    system_key=key,
                    status="active",
                    schema_version=1,
                    display_field_id=None,
                    created_by=0,
                    updated_by=0,
                    created_at=now,
                    updated_at=now,
                )
            )
        elif existing != table_id:
            raise RuntimeError(f"System data table key {key!r} is already assigned")


def downgrade():
    bind = op.get_bind()
    system_ids = [table_id for table_id, _, _ in SYSTEM_TABLES]
    fields = sa.table("v3_data_fields", sa.column("table_id", sa.Uuid()))
    records = sa.table("v3_data_records", sa.column("table_id", sa.Uuid()))
    if bind.scalar(
        sa.select(sa.func.count()).select_from(fields).where(fields.c.table_id.in_(system_ids))
    ):
        raise RuntimeError("Cannot downgrade 0036 after system extension fields were created")
    if bind.scalar(
        sa.select(sa.func.count()).select_from(records).where(records.c.table_id.in_(system_ids))
    ):
        raise RuntimeError("Cannot downgrade 0036 after system extension values were created")
    bind.execute(
        sa.text("DELETE FROM v3_data_tables WHERE system_key IN ('products','suppliers','orders')")
    )
    with op.batch_alter_table("v3_data_records") as batch:
        batch.drop_constraint("uq_data_record_source_record_id", type_="unique")
        batch.drop_constraint("ck_data_record_source_record_id", type_="check")
        batch.drop_column("source_record_id")
    with op.batch_alter_table("v3_data_tables") as batch:
        batch.drop_constraint("uq_data_table_system_key", type_="unique")
        batch.drop_constraint("ck_data_table_system_key", type_="check")
        batch.drop_constraint("ck_data_table_kind", type_="check")
        batch.drop_column("system_key")
        batch.drop_column("table_kind")
