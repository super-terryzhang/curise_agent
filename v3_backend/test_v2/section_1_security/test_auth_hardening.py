"""Regression assertions: unauthorized channels must reject protected operations.
All users, tokens and files here are synthetic, in-memory/local test fixtures.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_v2.fixtures.helpers import seed_user as make_user
from test_v2.section_1_security.test_user_capabilities import _seed_order


def signin(client, email):
    r = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert r.status_code == 200
    return r.json()


def headers(data):
    return {"Authorization": "Bearer " + data["access_token"]}


def test_financial_capability_enforced_by_agent(client, db):
    from agent.runtime.deps import V3Deps
    from agent.runtime.factory import _toolsets_for_role
    from agent.runtime.tools.orders import get_order_financials

    user = make_user(db, email="nocap@example.test")
    oid = _seed_order(db, user_id=user.id)
    data = signin(client, "nocap@example.test")
    assert data["user"]["capabilities"] == []
    assert client.get(f"/api/orders/{oid}/financials", headers=headers(data)).status_code == 403
    assert "business" in _toolsets_for_role("employee")
    result = get_order_financials(
        oid,
        ctx=SimpleNamespace(
            extras={"v3_deps": V3Deps(db=db, user_id=user.id, user_role="employee")}
        ),
    )
    assert result.startswith("Error:")


def test_deactivated_account_cannot_reach_line_handler(client, db, monkeypatch):
    from apps.line import handlers
    from domains.line.service import bind_user

    user = make_user(db, email="lineinactive@example.test")
    data = signin(client, "lineinactive@example.test")
    bind_user(db, line_user_id="AUDIT_LINE_ONLY", channel_id="default", internal_user_id=user.id)
    user.is_active = False
    db.commit()
    assert client.get("/api/auth/me", headers=headers(data)).status_code == 401
    bound = AsyncMock()
    monkeypatch.setattr(handlers, "_handle_bound_dm", bound)
    event = SimpleNamespace(source=SimpleNamespace(user_id="AUDIT_LINE_ONLY", channel_id="default"))
    asyncio.run(handlers._handle_dm_event(event=event, db=db, platform=SimpleNamespace()))
    bound.assert_not_awaited()


def test_financial_view_grant_also_permits_write(client, db):
    from domains.identity.models import UserCapability

    user = make_user(db, email="viewonly@example.test")
    oid = _seed_order(db, user_id=user.id)
    db.add(UserCapability(user_id=user.id, capability="financials.view"))
    db.commit()
    data = signin(client, "viewonly@example.test")
    r = client.patch(
        f"/api/orders/{oid}/financial-settings", headers=headers(data), json={"tax_rate": 0.13}
    )
    assert r.status_code == 200


def test_order_detail_filters_financial_data_without_capability(client, db):
    from domains.orders.models import Order

    user = make_user(db, email="detailnocap@example.test")
    oid = _seed_order(db, user_id=user.id)
    order = db.get(Order, oid)
    order.financial_data = {"audit_secret_margin": 0.42}
    db.commit()
    data = signin(client, "detailnocap@example.test")
    assert client.get(f"/api/orders/{oid}/financials", headers=headers(data)).status_code == 403
    r = client.get(f"/api/orders/{oid}", headers=headers(data))
    assert r.status_code == 200
    assert r.json()["financial_data"] is None


@pytest.mark.parametrize("disable_during_run", [False, True])
def test_line_delivery_rechecks_account_after_model(db, monkeypatch, disable_during_run):
    from apps.line import handlers

    user = make_user(db, email="line-delivery@example.test")
    event = SimpleNamespace(
        event_type="message",
        message_type="text",
        text="合成测试消息",
        reply_token="synthetic",
        source=SimpleNamespace(user_id="synthetic-line"),
    )
    platform = SimpleNamespace(
        show_loading=AsyncMock(), reply_text=AsyncMock(), reply_flex=AsyncMock()
    )
    monkeypatch.setattr(handlers, "_resolve_session", lambda *args, **kwargs: "synthetic-session")

    # The worker result is synthetic. Commit on the test thread before result delivery.
    async def run(*args, **kwargs):
        if disable_during_run:
            user.is_active = False
            db.commit()
        return "合成业务回复"

    monkeypatch.setattr(handlers.asyncio, "to_thread", run)
    asyncio.run(
        handlers._handle_bound_dm(
            event=event, db=db, line_user_id=user.id, line_user_role=user.role, platform=platform
        )
    )
    assert platform.reply_text.await_count == (0 if disable_during_run else 1)
    platform.reply_flex.assert_not_awaited()
