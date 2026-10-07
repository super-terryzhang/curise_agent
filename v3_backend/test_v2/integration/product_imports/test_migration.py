"""0036→0037 must preserve business rows while installing import storage."""

from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from infrastructure.config import settings
from infrastructure.db.base import Base
from infrastructure.db.model_registry import import_all_models


def alembic_config():
    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    return config


def install_0036_shape(engine):
    import_all_models()
    Base.metadata.create_all(
        engine,
        tables=[table for name, table in Base.metadata.tables.items() if not name.startswith("v3_import_")],
    )
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX IF EXISTS uq_products_active_normalized_code_port"))
        connection.execute(text("DROP INDEX IF EXISTS uq_data_fields_active_normalized_label"))
        connection.execute(text("ALTER TABLE v3_product_price_periods DROP COLUMN IF EXISTS revision"))
        country_id = connection.scalar(
            text("INSERT INTO countries (name,status) VALUES ('日本',true) RETURNING id")
        )
        port_id = connection.scalar(
            text("INSERT INTO ports (name,country_id,status) VALUES ('大阪',:country,true) RETURNING id"),
            {"country": country_id},
        )
        product_id = connection.scalar(
            text(
                "INSERT INTO products "
                "(product_name_en,code,country_id,port_id,status,revision,price_version) "
                "VALUES ('保留产品','KEEP-1',:country,:port,true,1,0) RETURNING id"
            ),
            {"country": country_id, "port": port_id},
        )
        connection.execute(
            text(
                "INSERT INTO v3_product_price_periods "
                "(product_id,price_type,amount,currency,effective_from,effective_to,status,source) "
                "VALUES (:product,'purchase',100,'JPY','2026-01-01','2026-01-31',true,'test')"
            ),
            {"product": product_id},
        )
    return product_id


def test_0036_to_0037_preserves_rows_and_round_trips(pg_isolated_url, monkeypatch):
    engine = create_engine(pg_isolated_url)
    product_id = install_0036_shape(engine)
    monkeypatch.setattr(settings, "DATABASE_URL", pg_isolated_url.render_as_string(hide_password=False))
    config = alembic_config()
    command.stamp(config, "0036_unified_data_tables")

    command.upgrade(config, "0037_temporary_product_import")
    inspector = inspect(engine)
    assert {"v3_import_batches", "v3_import_rows", "v3_import_changes"}.issubset(
        inspector.get_table_names()
    )
    assert "revision" in {c["name"] for c in inspector.get_columns("v3_product_price_periods")}
    assert "uq_products_active_normalized_code_port" in {
        index["name"] for index in inspector.get_indexes("products")
    }
    assert "uq_data_fields_active_normalized_label" in {
        index["name"] for index in inspector.get_indexes("v3_data_fields")
    }
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT product_name_en FROM products WHERE id=:id"), {"id": product_id}
        ) == "保留产品"
        assert connection.scalar(text("SELECT revision FROM v3_product_price_periods")) == 1
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0037_temporary_product_import"
        )

    table_id = UUID("025588dd-ae63-5607-9e78-1179a500ed6e")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO v3_data_tables "
                "(id,name,table_kind,system_key,status,schema_version,created_by,updated_by,"
                "created_at,updated_at) "
                "VALUES (:id,'产品','system','products','active',1,0,0,now(),now())"
            ),
            {"id": table_id},
        )
        connection.execute(
            text(
                "INSERT INTO v3_data_fields "
                "(id,table_id,label,field_type,required,\"unique\",config,sort_order,status,"
                "schema_version,created_at,updated_at) VALUES "
                "('10000000-0000-0000-0000-000000000001',:table,'Business Class','text',"
                "false,false,'{}',0,'active',1,now(),now())"
            ),
            {"table": table_id},
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO v3_data_fields "
                "(id,table_id,label,field_type,required,\"unique\",config,sort_order,status,"
                "schema_version,created_at,updated_at) VALUES "
                "('10000000-0000-0000-0000-000000000002',:table,' business class ',"
                "'text',false,false,'{}',1,'active',1,now(),now())"
            ),
            {"table": table_id},
        )
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO v3_data_fields "
                "(id,table_id,label,field_type,required,\"unique\",config,sort_order,status,"
                "schema_version,created_at,updated_at) VALUES "
                "('10000000-0000-0000-0000-000000000003',:table,' business class ','text',"
                "false,false,'{}',2,'archived',1,now(),now())"
            ),
            {"table": table_id},
        )

    command.downgrade(config, "0036_unified_data_tables")
    assert not {"v3_import_batches", "v3_import_rows", "v3_import_changes"}.intersection(
        inspect(engine).get_table_names()
    )
    command.upgrade(config, "0037_temporary_product_import")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM products")) == 1
    engine.dispose()
