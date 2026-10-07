"""Restricted PostgreSQL fixture for temporary product-import integration tests."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@pytest.fixture
def pg_isolated_url():
    raw = os.environ.get("DATA_TABLES_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("DATA_TABLES_TEST_DATABASE_URL 未配置：未验证 PostgreSQL")
    url = make_url(raw)
    allowed = {(55447, "cruise_data_tables_test"), (5432, "cruise_migration_test")}
    if (
        url.get_backend_name() != "postgresql"
        or url.host != "127.0.0.1"
        or (url.port, url.database) not in allowed
        or url.query
    ):
        pytest.fail("只允许显式指定的本地测试数据库，不允许生产代理或附加连接参数")
    schema = "product_import_test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = url.update_query_dict({"options": f"-csearch_path={schema}"})
    try:
        yield isolated
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()

