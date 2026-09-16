"""Run explicitly against a disposable localhost database, never production."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from agent.runtime.deps import V3Deps
from agent.runtime.tools.query_db import query_db
from apps.http.auth_limits import consume
from domains.identity import service
from domains.identity.models import RefreshToken, User
from domains.masterdata.models import Product
from infrastructure.db.base import Base
from test_v2.fixtures.helpers import seed_user

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"), reason="explicit disposable PostgreSQL required"
)


@pytest.fixture
def engine():
    url = make_url(os.environ["TEST_POSTGRES_URL"])
    assert (
        url.host == "127.0.0.1" and url.port == 55439 and url.database == "cruise_auth_validation"
    )
    engine = create_engine(url, pool_size=12)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_ten_refreshes_have_one_successor(engine, db):
    user = seed_user(db, email="pg-refresh@example.test")
    first = service.login(db, email=user.email, password="password123")
    barrier = Barrier(10)

    def refresh(_):
        with Session(engine) as session:
            barrier.wait()
            return service.refresh_tokens(session, refresh_token=first.refresh_token).refresh_token

    with ThreadPoolExecutor(max_workers=10) as pool:
        tokens = list(pool.map(refresh, range(10)))
    assert len(set(tokens)) == 1
    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(RefreshToken)
                .where(RefreshToken.is_revoked.is_(False))
            )
            == 1
        )


def test_reset_refresh_race_revokes_every_old_session(engine, db):
    user = seed_user(db, email="pg-reset@example.test")
    first = service.login(db, email=user.email, password="password123")
    barrier = Barrier(2)

    def act(action):
        with Session(engine) as session:
            barrier.wait()
            try:
                if action == "reset":
                    service.reset_user_password(session, user_id=user.id)
                else:
                    service.refresh_tokens(session, refresh_token=first.refresh_token)
            except service.InvalidRefreshToken:
                session.rollback()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(act, ["reset", "refresh"]))
    with Session(engine) as session:
        with pytest.raises(service.InvalidRefreshToken):
            service.get_user_from_access_token(session, access_token=first.access_token)
        assert (
            session.scalar(
                select(func.count())
                .select_from(RefreshToken)
                .where(RefreshToken.is_revoked.is_(False))
            )
            == 0
        )


def test_two_password_changes_only_one_consumes_old_password(engine, db):
    user = seed_user(db, email="pg-change@example.test")
    first = service.login(db, email=user.email, password="password123")
    barrier = Barrier(2)

    def change(index):
        with Session(engine) as session:
            target = session.get(User, user.id)
            barrier.wait()
            try:
                service.change_password(
                    session,
                    user=target,
                    current_password="password123",
                    new_password=f"new-concurrent-password-{index}",
                )
                return True
            except service.WrongCurrentPassword:
                session.rollback()
                return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(change, [1, 2])) == 1
    with Session(engine) as session, pytest.raises(service.InvalidRefreshToken):
        service.get_user_from_access_token(session, access_token=first.access_token)


def test_concurrent_failed_logins_preserve_account_counter(engine, db):
    user = seed_user(db, email="pg-failures@example.test")
    barrier = Barrier(4)

    def fail(_):
        with Session(engine) as session:
            barrier.wait()
            with pytest.raises(service.InvalidCredentials):
                service.login(session, email=user.email, password="incorrect")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fail, range(4)))
    with Session(engine) as session:
        assert session.get(User, user.id).failed_login_attempts == 4


def test_refresh_logout_race_never_resurrects_session(engine, db):
    user = seed_user(db, email="pg-logout@example.test")
    first = service.login(db, email=user.email, password="password123")
    barrier = Barrier(2)

    def act(action):
        with Session(engine) as session:
            barrier.wait()
            try:
                if action == "logout":
                    service.logout(session, refresh_token=first.refresh_token)
                else:
                    service.refresh_tokens(session, refresh_token=first.refresh_token)
            except service.InvalidRefreshToken:
                session.rollback()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(act, ["logout", "refresh"]))
    with Session(engine) as session:
        with pytest.raises(service.InvalidRefreshToken):
            service.get_user_from_access_token(session, access_token=first.access_token)
        assert (
            session.scalar(
                select(func.count())
                .select_from(RefreshToken)
                .where(RefreshToken.is_revoked.is_(False))
            )
            == 0
        )


def test_two_root_changes_keep_one_active_root(engine, db):
    first = seed_user(db, email="pg-root1@example.test", role="superadmin")
    second = seed_user(db, email="pg-root2@example.test", role="superadmin")
    ids = [first.id, second.id]
    barrier = Barrier(2)

    def disable(user_id):
        with Session(engine) as session:
            barrier.wait()
            try:
                service.update_user(session, user_id=user_id, is_active=False)
                return True
            except service.CannotDeactivateSelf:
                session.rollback()
                return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(disable, ids))
    assert results.count(True) == 1
    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.role == "superadmin", User.is_active.is_(True))
            )
            == 1
        )


def test_rate_counter_shared_across_connections(engine):
    barrier = Barrier(10)

    def hit(_):
        with Session(engine) as session:
            barrier.wait()
            return consume(session, "synthetic-shared-source", 5, 900)

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(hit, range(10)))
    assert sum(value > 0 for value in results) == 5


def test_business_query_on_postgres_preserves_outer_session(db):
    root = seed_user(db, email="pg-sql@example.test", role="admin")
    db.add(Product(product_name_en="Synthetic", code="A", price=10))
    db.commit()
    ctx = SimpleNamespace(extras={"v3_deps": V3Deps(db=db, user_id=root.id)})
    assert query_db("SELECT hashed_password FROM users", ctx=ctx).startswith("Error:")
    assert query_db("SELECT 1 / 0 AS invalid", ctx=ctx).startswith("Error:")
    result = query_db(
        "WITH p AS (SELECT price FROM products) SELECT sum(price) AS total FROM p", ctx=ctx
    )
    from decimal import Decimal

    assert Decimal(str(json.loads(result)["rows"][0]["total"])) == Decimal("10")
    assert db.get(User, root.id) is not None


def test_sql_execution_and_result_bounds(db):
    import time

    root = seed_user(db, email="pg-timeout@example.test", role="admin")
    db.add_all([Product(product_name_en="Synthetic", code=f"LIMIT-{i}") for i in range(200)])
    db.commit()
    ctx = SimpleNamespace(extras={"v3_deps": V3Deps(db=db, user_id=root.id)})
    started = time.monotonic()
    result = query_db(
        "SELECT count(*) FROM products a CROSS JOIN products b CROSS JOIN products c CROSS JOIN products d CROSS JOIN products e",
        ctx=ctx,
    )
    assert result.startswith("Error:")
    assert time.monotonic() - started < 8
    valid = json.loads(query_db("SELECT code FROM products", ctx=ctx))
    assert len(valid["rows"]) == 50 and valid["truncated"] is True
