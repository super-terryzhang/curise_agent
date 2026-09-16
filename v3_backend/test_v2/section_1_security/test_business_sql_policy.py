"""SQL must not expose security data, even through CTEs or functions."""

import json
from types import SimpleNamespace

import pytest

from agent.runtime.deps import V3Deps
from agent.runtime.tools.query_db import query_db
from domains.masterdata.models import Product
from test_v2.fixtures.helpers import seed_user


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT hashed_password FROM users",
        "WITH x AS (SELECT * FROM users) SELECT * FROM x",
        "SELECT p.id FROM products p JOIN users u ON p.id=u.id",
        "SELECT * FROM pg_catalog.pg_authid",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT set_config('role', 'postgres', false)",
        "SELECT * FROM products FOR UPDATE",
        "SELECT 1; DELETE FROM products",
        "SELECT financial_data FROM orders",
        "SELECT * FROM v3_security_events",
        "WITH RECURSIVE x AS (SELECT 1 UNION ALL SELECT 1 FROM x) SELECT * FROM x",
        "SELECT CAST(1 AS regclass)",
    ],
)
def test_disallowed_query_returns_no_rows(db, sql):
    root = seed_user(db, email="sqlroot@example.test", role="superadmin")
    ctx = SimpleNamespace(extras={"v3_deps": V3Deps(db=db, user_id=root.id)})
    assert query_db(sql, ctx=ctx).startswith("Error:")


def test_legitimate_cte_join_and_aggregate_survive(db):
    root = seed_user(db, email="sqlroot@example.test", role="admin")
    db.add(Product(product_name_en="Synthetic item", code="A1", price=12))
    db.commit()
    ctx = SimpleNamespace(extras={"v3_deps": V3Deps(db=db, user_id=root.id)})
    result = query_db(
        "WITH p AS (SELECT code, price FROM products) SELECT count(*) AS n, sum(price) AS total FROM p",
        ctx=ctx,
    )
    assert json.loads(result)["rows"] == [{"n": 1, "total": 12}]
    assert query_db("SELECT missing_column FROM products", ctx=ctx).startswith("Error:")
    assert json.loads(query_db("SELECT count(*) AS n FROM products", ctx=ctx))["rows"][0]["n"] == 1


def test_current_role_checked_at_execution(db):
    user = seed_user(db, email="sqlemployee@example.test", role="employee")
    ctx = SimpleNamespace(
        extras={"v3_deps": V3Deps(db=db, user_id=user.id, user_role="superadmin")}
    )
    assert query_db("SELECT 1", ctx=ctx).startswith("Error:")
