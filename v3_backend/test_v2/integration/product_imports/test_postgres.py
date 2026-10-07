"""PostgreSQL proves the normalized active-product identity constraint."""

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from infrastructure.config import settings
from test_v2.integration.product_imports.test_migration import (
    alembic_config,
    install_0036_shape,
)


def test_active_normalized_code_and_port_are_unique(pg_isolated_url, monkeypatch):
    engine = create_engine(pg_isolated_url)
    install_0036_shape(engine)
    monkeypatch.setattr(settings, "DATABASE_URL", pg_isolated_url.render_as_string(hide_password=False))
    config = alembic_config()
    command.stamp(config, "0036_unified_data_tables")
    command.upgrade(config, "0037_temporary_product_import")
    with engine.begin() as connection:
        country_id = connection.scalar(text("SELECT id FROM countries LIMIT 1"))
        first_port = connection.scalar(text("SELECT id FROM ports WHERE name='大阪'"))
        second_port = connection.scalar(
            text("INSERT INTO ports (name,country_id,status) VALUES ('横浜',:country,true) RETURNING id"),
            {"country": country_id},
        )
        connection.execute(
            text(
                "INSERT INTO products "
                "(product_name_en,code,country_id,port_id,status,revision,price_version) "
                "VALUES ('别港产品',' keep-1 ',:country,:port,true,1,0)"
            ),
            {"country": country_id, "port": second_port},
        )
        connection.execute(
            text(
                "INSERT INTO products "
                "(product_name_en,code,country_id,port_id,status,revision,price_version) "
                "VALUES ('停用重复',' KEEP-1 ',:country,:port,false,1,0)"
            ),
            {"country": country_id, "port": first_port},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO products "
                "(product_name_en,code,country_id,port_id,status,revision,price_version) "
                "VALUES ('启用重复',' keep-1 ',:country,:port,true,1,0)"
            ),
            {"country": country_id, "port": first_port},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text("UPDATE products SET status=true WHERE product_name_en='停用重复'"))
    engine.dispose()
