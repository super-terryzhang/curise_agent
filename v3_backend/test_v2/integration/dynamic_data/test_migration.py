"""Incremental DDL must preserve 0035 data and every existing business row."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, inspect, select, text
from sqlalchemy.orm import Session

from domains.masterdata.models import Product, ProductPricePeriod, Supplier
from domains.orders.models import Order
from infrastructure.config import settings
from infrastructure.db.base import Base

SYSTEM_TABLE_IDS = {
    "products": UUID("025588dd-ae63-5607-9e78-1179a500ed6e"),
    "suppliers": UUID("480cc5e5-5882-58d1-a227-e8ee734c866f"),
    "orders": UUID("0bc67ecc-ccd3-53b5-8327-74f4b482b7b4"),
}
DYNAMIC_TABLES = (
    "v3_data_tables",
    "v3_data_fields",
    "v3_data_records",
    "v3_data_links",
    "v3_data_unique_values",
    "v3_data_changes",
)


def alembic_config():
    root = Path(__file__).resolve().parents[3]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    return cfg


def test_0035_to_0036_preserves_user_tables_and_seeds_system_catalog(pg_isolated_url, monkeypatch):
    e = create_engine(pg_isolated_url)
    old = [t for n, t in Base.metadata.tables.items() if not n.startswith("v3_data_")]
    Base.metadata.create_all(e, tables=old)
    with Session(e) as db:
        supplier = Supplier(name="迁移保留供应商")
        db.add(supplier)
        db.flush()
        p = Product(
            product_name_en="迁移保留产品",
            price=Decimal("123.45"),
            supplier_id=supplier.id,
        )
        order = Order(user_id=701, filename="migration-po.pdf", po_number="PO-MIGRATION")
        db.add_all([p, order])
        db.flush()
        db.add(
            ProductPricePeriod(
                product_id=p.id,
                price_type="purchase",
                amount=Decimal("123.45"),
                currency="JPY",
                effective_from=date(2026, 1, 1),
                effective_to=date(2026, 3, 31),
            )
        )
        db.commit()

    def business_snapshot():
        with e.connect() as conn:
            return {t.name: [dict(row) for row in conn.execute(select(t)).mappings()] for t in old}

    before_business = business_snapshot()
    columns = {t.name: [c["name"] for c in inspect(e).get_columns(t.name)] for t in old}
    monkeypatch.setattr(
        settings, "DATABASE_URL", pg_isolated_url.render_as_string(hide_password=False)
    )
    cfg = alembic_config()
    command.stamp(cfg, "0034_drop_product_validity")
    command.upgrade(cfg, "0035_custom_data_tables")

    metadata_0035 = MetaData()
    metadata_0035.reflect(e, only=DYNAMIC_TABLES)
    tables = metadata_0035.tables
    now = datetime(2026, 10, 6, tzinfo=UTC)
    table_a = UUID("2b569643-d704-4be4-8e4f-34cf70ea99e0")
    table_b = UUID("4ea60558-b239-48bd-8716-997f806c0408")
    text_field = UUID("171c84fe-113c-429d-a7fb-3a450a362f13")
    link_field = UUID("2ae2fda5-55bb-4227-8cdf-1eebc3af49f3")
    record_a = UUID("7b728ce5-c51b-4be5-a93a-042ed85b3213")
    record_b = UUID("dd7ef867-e15f-4dbb-8092-c4af91452c93")
    with e.begin() as conn:
        conn.execute(
            tables["v3_data_tables"].insert(),
            [
                {
                    "id": table_a,
                    "name": "迁移用户表 A",
                    "description": "保留",
                    "status": "active",
                    "schema_version": 3,
                    "display_field_id": None,
                    "created_by": 11,
                    "updated_by": 12,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": table_b,
                    "name": "迁移用户表 B",
                    "description": None,
                    "status": "active",
                    "schema_version": 2,
                    "display_field_id": None,
                    "created_by": 11,
                    "updated_by": 11,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
        conn.execute(
            tables["v3_data_fields"].insert(),
            [
                {
                    "id": text_field,
                    "table_id": table_a,
                    "label": "编号",
                    "field_type": "text",
                    "required": True,
                    "unique": True,
                    "default_value": None,
                    "config": {"max_length": 40, "multiline": False},
                    "target_table_id": None,
                    "sort_order": 0,
                    "status": "active",
                    "schema_version": 1,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": link_field,
                    "table_id": table_a,
                    "label": "关联",
                    "field_type": "link",
                    "required": False,
                    "unique": False,
                    "default_value": None,
                    "config": {},
                    "target_table_id": table_b,
                    "sort_order": 1,
                    "status": "active",
                    "schema_version": 1,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
        conn.execute(
            tables["v3_data_records"].insert(),
            [
                {
                    "id": record_a,
                    "table_id": table_a,
                    "values": {str(text_field): "保留编号"},
                    "revision": 2,
                    "schema_version": 3,
                    "status": "active",
                    "created_by": 11,
                    "updated_by": 12,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": record_b,
                    "table_id": table_b,
                    "values": {},
                    "revision": 1,
                    "schema_version": 2,
                    "status": "active",
                    "created_by": 11,
                    "updated_by": 11,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
        conn.execute(
            tables["v3_data_links"].insert(),
            {
                "table_id": table_a,
                "record_id": record_a,
                "field_id": link_field,
                "target_table_id": table_b,
                "target_record_id": record_b,
            },
        )
        conn.execute(
            tables["v3_data_unique_values"].insert(),
            {
                "table_id": table_a,
                "record_id": record_a,
                "field_id": text_field,
                "value": "保留编号",
            },
        )
        conn.execute(
            tables["v3_data_changes"].insert(),
            {
                "id": UUID("b624b6d4-3659-4627-bd47-9990b9232492"),
                "table_id": table_a,
                "field_id": None,
                "record_id": record_a,
                "entity_type": "record",
                "entity_id": record_a,
                "action": "updated",
                "before": {"values": {str(text_field): "旧编号"}},
                "after": {"values": {str(text_field): "保留编号"}},
                "display_snapshot": {"fields": {}},
                "creation_request": None,
                "actor_id": 12,
                "actor_role": "admin",
                "schema_version": 3,
                "revision": 2,
                "created_at": now,
            },
        )
        conn.execute(
            tables["v3_data_tables"]
            .update()
            .where(tables["v3_data_tables"].c.id == table_a)
            .values(display_field_id=text_field)
        )

    def dynamic_snapshot():
        with e.connect() as conn:
            snapshot = {}
            for name in DYNAMIC_TABLES:
                statement = select(*tables[name].c)
                if name == "v3_data_tables":
                    statement = statement.where(tables[name].c.id.in_((table_a, table_b)))
                snapshot[name] = [
                    dict(row)
                    for row in conn.execute(
                        statement.order_by(*tables[name].primary_key.columns)
                    ).mappings()
                ]
            return snapshot

    before_dynamic = dynamic_snapshot()
    command.upgrade(cfg, "0036_unified_data_tables")
    command.upgrade(cfg, "0036_unified_data_tables")

    assert business_snapshot() == before_business
    assert dynamic_snapshot() == before_dynamic
    inspector = inspect(e)
    assert {t.name: [c["name"] for c in inspector.get_columns(t.name)] for t in old} == columns
    assert {n for n in inspector.get_table_names() if n.startswith("v3_data_")} == set(
        DYNAMIC_TABLES
    )
    assert (
        str(
            next(c for c in inspector.get_columns("v3_data_records") if c["name"] == "values")[
                "type"
            ]
        )
        == "JSONB"
    )
    assert len(inspector.get_foreign_keys("v3_data_links")) >= 3
    with e.connect() as conn:
        assert (
            conn.scalar(text("SELECT version_num FROM alembic_version"))
            == "0036_unified_data_tables"
        )
        rows = conn.execute(
            text(
                "SELECT id, system_key, table_kind, created_by, updated_by "
                "FROM v3_data_tables WHERE system_key IS NOT NULL ORDER BY system_key"
            )
        ).mappings()
        assert [dict(row) for row in rows] == [
            {
                "id": SYSTEM_TABLE_IDS[key],
                "system_key": key,
                "table_kind": "system",
                "created_by": 0,
                "updated_by": 0,
            }
            for key in sorted(SYSTEM_TABLE_IDS)
        ]
        assert (
            conn.scalar(text("SELECT count(*) FROM v3_data_tables WHERE table_kind = 'user'")) == 2
        )
    e.dispose()


def test_new_module_ddl_installs_in_empty_schema(pg_isolated_url, monkeypatch):
    monkeypatch.setattr(
        settings, "DATABASE_URL", pg_isolated_url.render_as_string(hide_password=False)
    )
    cfg = alembic_config()
    command.stamp(cfg, "0034_drop_product_validity")
    command.upgrade(cfg, "0036_unified_data_tables")
    e = create_engine(pg_isolated_url)
    assert set(inspect(e).get_table_names()) == {
        "alembic_version",
        "v3_data_tables",
        "v3_data_fields",
        "v3_data_records",
        "v3_data_links",
        "v3_data_unique_values",
        "v3_data_changes",
    }
    with e.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM v3_data_tables")) == 3
    e.dispose()
