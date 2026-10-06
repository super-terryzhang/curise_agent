from __future__ import annotations

import os
import re
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from scripts.audit_fresh_database import AuditFailed, audit_database
from scripts.bootstrap_fresh_database import BootstrapRefused, bootstrap_database


@pytest.fixture
def disposable_database_url():
    raw = os.environ.get("FRESH_BOOTSTRAP_TEST_ADMIN_URL")
    if not raw:
        pytest.skip("FRESH_BOOTSTRAP_TEST_ADMIN_URL 未配置：未验证空库安装")
    admin_url = make_url(raw)
    if (
        admin_url.get_backend_name() != "postgresql"
        or admin_url.host != "127.0.0.1"
        or admin_url.port not in {5432, 55447}
        or admin_url.database not in {"cruise_data_tables_test", "cruise_migration_test"}
    ):
        pytest.fail("空库安装测试只允许显式列出的本机 PostgreSQL 测试服务")

    database = "cruise_fresh_test_" + uuid4().hex
    assert re.fullmatch(r"cruise_fresh_test_[0-9a-f]{32}", database)
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(sa.text(f'CREATE DATABASE "{database}"'))
    test_url = admin_url.set(database=database)
    try:
        yield test_url
    finally:
        with admin.connect() as connection:
            connection.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database AND pid <> pg_backend_pid()"
                ),
                {"database": database},
            )
            connection.execute(sa.text(f'DROP DATABASE "{database}"'))
        admin.dispose()


def test_fresh_install_audit_refusal_and_pollution_detection(disposable_database_url):
    database = disposable_database_url.database
    engine = sa.create_engine(disposable_database_url)
    try:
        result = bootstrap_database(
            engine,
            expected_database=database,
            admin_email="clean-admin@example.test",
            admin_password="LocalOnly!2026-Change",
        )
        assert result["alembic_head"] == "0036_unified_data_tables"
        assert result["model_tables"] == 51

        audit = audit_database(
            engine,
            expected_database=database,
            expected_admin_email="clean-admin@example.test",
        )
        assert audit == {
            "database": database,
            "alembic_head": "0036_unified_data_tables",
            "tables": 53,
            "system_catalog_rows": 3,
            "admin_rows": 1,
            "business_rows": 0,
        }

        with pytest.raises(BootstrapRefused, match="not empty"):
            bootstrap_database(engine, expected_database=database)

        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO products (product_name_en, price, currency, status) "
                    "VALUES ('pollution sentinel', 1, 'JPY', true)"
                )
            )
        with pytest.raises(AuditFailed, match=r"products contains 1 unexpected row\(s\)"):
            audit_database(
                engine,
                expected_database=database,
                expected_admin_email="clean-admin@example.test",
            )
    finally:
        engine.dispose()
