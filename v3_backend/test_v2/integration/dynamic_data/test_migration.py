"""Incremental DDL must not rewrite existing business tables or price periods."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from domains.masterdata.models import Product, ProductPricePeriod
from infrastructure.config import settings
from infrastructure.db.base import Base


def alembic_config():
    root = Path(__file__).resolve().parents[3]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    return cfg


def test_upgrade_preserves_existing_tables_and_prices(pg_isolated_url, monkeypatch):
    e = create_engine(pg_isolated_url)
    old = [t for n, t in Base.metadata.tables.items() if not n.startswith("v3_data_")]
    Base.metadata.create_all(e, tables=old)
    with Session(e) as db:
        p = Product(product_name_en="迁移保留产品", price=Decimal("123.45"))
        db.add(p)
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

    def snapshot():
        with e.connect() as conn:
            return {t.name: [dict(row) for row in conn.execute(select(t)).mappings()] for t in old}

    before = snapshot()
    columns = {t.name: [c["name"] for c in inspect(e).get_columns(t.name)] for t in old}
    monkeypatch.setattr(
        settings, "DATABASE_URL", pg_isolated_url.render_as_string(hide_password=False)
    )
    cfg = alembic_config()
    command.stamp(cfg, "0034_drop_product_validity")
    command.upgrade(cfg, "0035_custom_data_tables")
    assert snapshot() == before
    inspector = inspect(e)
    assert {t.name: [c["name"] for c in inspector.get_columns(t.name)] for t in old} == columns
    assert len([n for n in inspector.get_table_names() if n.startswith("v3_data_")]) == 6
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
            == "0035_custom_data_tables"
        )
    e.dispose()


def test_new_module_ddl_installs_in_empty_schema(pg_isolated_url, monkeypatch):
    monkeypatch.setattr(
        settings, "DATABASE_URL", pg_isolated_url.render_as_string(hide_password=False)
    )
    cfg = alembic_config()
    command.stamp(cfg, "0034_drop_product_validity")
    command.upgrade(cfg, "0035_custom_data_tables")
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
    e.dispose()
