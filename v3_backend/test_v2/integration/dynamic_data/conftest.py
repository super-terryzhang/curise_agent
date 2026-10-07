"""Dedicated foreign-key-enabled database; existing fixtures remain unchanged."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from infrastructure.config import settings
from infrastructure.db.base import Base


@pytest.fixture(autouse=True)
def enable_custom_tables(monkeypatch):
    monkeypatch.setattr(settings, "CUSTOM_DATA_TABLES_ENABLED", True)


@pytest.fixture
def data_db():
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(
        engine, tables=[t for n, t in Base.metadata.tables.items() if n.startswith("v3_data_")]
    )
    with Session(engine) as db:
        yield db
    engine.dispose()


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
    schema = "custom_test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = url.update_query_dict({"options": f"-csearch_path={schema}"})
    try:
        yield isolated
    finally:
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def pg_data_engine(pg_isolated_url):
    engine = create_engine(pg_isolated_url)
    Base.metadata.create_all(
        engine, tables=[t for n, t in Base.metadata.tables.items() if n.startswith("v3_data_")]
    )
    try:
        yield engine
    finally:
        engine.dispose()
