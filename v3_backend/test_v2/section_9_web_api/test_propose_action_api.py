"""Section 9 — Web API: `/api/chat/actions/{id}/decide` (HITL approval).

测试目标：
    HITL 是 v3 agent 安全模型的最后一道门：所有 Tier-2 操作（删除、财务
    变更、批量、跨用户）走 `propose_action` 写 PendingAction 行，前端弹
    审批卡，用户 click "approve"——/decide 才真正 dispatch 注册过的
    handler。这里验证：
    - approve → handler 被调用 + 结果落 result + status=approved
    - reject → handler 不被调用 + result 带 notes + status=rejected
    - already-decided → 409
    - 跨用户 action_id → 404 (silent)
    - 未知 action_id → 404
    - handler 抛异常 → status=failed + 错误持久化

为什么重要：
    HITL 出错就意味着 agent 可以绕过用户确认直接动数据。401/403/404/409
    各自的语义错位都是灾难。

设计方法：
    - 不通过 chat agent 调 propose_action（那条路要 LLM stub）；直接在 DB
      里 seed PendingAction，然后命 /decide，这样可以 isolated 测试 HTTP
      decide 端点本身。
    - 用 `register_action` 加测试 handler；fixture 用 snapshot/restore
      避免污染全局 ACTION_DISPATCH。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from agent.runtime import approvals
from agent.runtime.approvals import ActionSpec, register_action
from agent.storage.models import ChatSession, PendingAction
from test_v2.fixtures.helpers import login, seed_user


# ─── Fixtures ────────────────────────────────────────────────


@pytest.fixture
def _restore_dispatch():
    """Snapshot global ACTION_DISPATCH and restore after each test.

    Multiple tests register actions with the same names but different
    handlers — without restoration they'd contaminate each other.
    """
    snapshot = dict(approvals.ACTION_DISPATCH)
    yield
    approvals.ACTION_DISPATCH.clear()
    approvals.ACTION_DISPATCH.update(snapshot)


def _seed_pending(
    session_factory: sessionmaker,
    *,
    user_id: int,
    action: str,
    target_id: int | None = 5,
    payload: dict | None = None,
    summary: str = "test action",
) -> tuple[int, str]:
    """Insert a PendingAction row directly + its parent ChatSession.

    Returns (action_id, session_id). Bypasses the agent's propose_action
    tool so we can test the decide endpoint in isolation.
    """
    db = session_factory()
    try:
        sid = f"sess-{user_id}-{action}"
        # Avoid clashing if same (user, action) used twice across tests:
        existing = db.get(ChatSession, sid)
        if existing is None:
            db.add(ChatSession(id=sid, user_id=user_id, title="t"))
            db.commit()
        row = PendingAction(
            session_id=sid,
            user_id=user_id,
            action=action,
            target_kind="thing",
            target_id=target_id,
            payload=payload or {},
            summary=summary,
            status="pending",
        )
        db.add(row)
        db.commit()
        return row.id, sid
    finally:
        db.close()


def _login_user(
    client: TestClient, session_factory: sessionmaker, *, email: str
) -> tuple[int, dict[str, str]]:
    """Seed an employee + log in. Returns (user_id, auth_headers)."""
    db = session_factory()
    try:
        u = seed_user(db, email=email, role="employee")
        uid = u.id
    finally:
        db.close()
    headers = login(client, email)
    return uid, headers


# ─── approve ────────────────────────────────────────────────


def test_decide_approve_dispatches_handler_and_persists(
    client: TestClient, session_factory: sessionmaker, _restore_dispatch
):
    """approve → handler called once → status=approved + result persisted."""
    calls: list[dict[str, Any]] = []

    def handler(deps, target_id, payload):
        calls.append(
            {"user_id": deps.user_id, "target_id": target_id, "payload": payload}
        )
        return {"executed": True, "target_id": target_id}

    register_action(
        ActionSpec(
            name="s9b_approve",
            target_kind="thing",
            description="test",
            dispatch=handler,
        )
    )

    uid, auth = _login_user(client, session_factory, email="approver@example.com")
    aid, _ = _seed_pending(
        session_factory, user_id=uid, action="s9b_approve", target_id=42
    )

    r = client.post(
        f"/api/chat/actions/{aid}/decide",
        json={"decision": "approve"},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["result"] == {"executed": True, "target_id": 42}

    # Handler ran exactly once with the right target_id.
    assert len(calls) == 1
    assert calls[0]["target_id"] == 42
    assert calls[0]["user_id"] == uid

    # Row state persisted.
    db = session_factory()
    try:
        row = db.get(PendingAction, aid)
        assert row.status == "approved"
        assert row.decided_by == uid
        assert row.decided_at is not None
        assert row.result == {"executed": True, "target_id": 42}
    finally:
        db.close()


# ─── reject ─────────────────────────────────────────────────


def test_decide_reject_does_not_dispatch(
    client: TestClient, session_factory: sessionmaker, _restore_dispatch
):
    """reject → handler is NOT called; row → status=rejected + notes in result."""
    called: list[Any] = []

    register_action(
        ActionSpec(
            name="s9b_reject",
            target_kind="thing",
            description="test",
            dispatch=lambda deps, tid, p: called.append(tid),
        )
    )

    uid, auth = _login_user(client, session_factory, email="rejecter@example.com")
    aid, _ = _seed_pending(session_factory, user_id=uid, action="s9b_reject")

    r = client.post(
        f"/api/chat/actions/{aid}/decide",
        json={"decision": "reject", "notes": "no thanks"},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"
    assert called == [], "handler must NOT be dispatched on reject"

    db = session_factory()
    try:
        row = db.get(PendingAction, aid)
        assert row.status == "rejected"
        assert row.result == {"notes": "no thanks"}
        assert row.decided_by == uid
    finally:
        db.close()


# ─── already decided ────────────────────────────────────────


def test_decide_already_decided_returns_409(
    client: TestClient, session_factory: sessionmaker, _restore_dispatch
):
    """Deciding a non-pending row → 409 Conflict."""
    register_action(
        ActionSpec(
            name="s9b_409",
            target_kind="thing",
            description="test",
            dispatch=lambda deps, tid, p: {"ok": True},
        )
    )
    uid, auth = _login_user(client, session_factory, email="twice@example.com")
    aid, _ = _seed_pending(session_factory, user_id=uid, action="s9b_409")

    r1 = client.post(
        f"/api/chat/actions/{aid}/decide",
        json={"decision": "approve"},
        headers=auth,
    )
    assert r1.status_code == 200

    r2 = client.post(
        f"/api/chat/actions/{aid}/decide",
        json={"decision": "approve"},
        headers=auth,
    )
    assert r2.status_code == 409
    assert "已被处理" in r2.json()["detail"]


# ─── cross-user blocked ─────────────────────────────────────


def test_decide_cross_user_blocked_returns_404(
    client: TestClient, session_factory: sessionmaker, _restore_dispatch
):
    """User B can't decide user A's action — silent 404."""
    register_action(
        ActionSpec(
            name="s9b_xuser",
            target_kind="thing",
            description="test",
            dispatch=lambda deps, tid, p: {"ok": True},
        )
    )

    uid_a, _ = _login_user(client, session_factory, email="alice-x@example.com")
    _, auth_b = _login_user(client, session_factory, email="bob-x@example.com")
    aid, _ = _seed_pending(session_factory, user_id=uid_a, action="s9b_xuser")

    r = client.post(
        f"/api/chat/actions/{aid}/decide",
        json={"decision": "approve"},
        headers=auth_b,
    )
    assert r.status_code == 404

    # Row still pending — Bob's request must not have written anything.
    db = session_factory()
    try:
        row = db.get(PendingAction, aid)
        assert row.status == "pending"
    finally:
        db.close()


# ─── unknown action_id ─────────────────────────────────────


def test_decide_unknown_action_id_returns_404(
    client: TestClient, session_factory: sessionmaker, _restore_dispatch
):
    """No PendingAction with that id (or it doesn't belong to user) → 404."""
    _, auth = _login_user(client, session_factory, email="ghost@example.com")

    r = client.post(
        "/api/chat/actions/9999999/decide",
        json={"decision": "approve"},
        headers=auth,
    )
    assert r.status_code == 404


# ─── handler raises ─────────────────────────────────────────


def test_decide_failed_dispatch_records_error(
    client: TestClient, session_factory: sessionmaker, _restore_dispatch
):
    """When the registered handler raises → row.status='failed', result has
    error message; client gets 500."""

    def bad_handler(deps, tid, payload):
        raise ValueError("kaboom")

    register_action(
        ActionSpec(
            name="s9b_fail",
            target_kind="thing",
            description="test",
            dispatch=bad_handler,
        )
    )

    uid, auth = _login_user(client, session_factory, email="failer@example.com")
    aid, _ = _seed_pending(session_factory, user_id=uid, action="s9b_fail")

    r = client.post(
        f"/api/chat/actions/{aid}/decide",
        json={"decision": "approve"},
        headers=auth,
    )
    assert r.status_code == 500

    db = session_factory()
    try:
        row = db.get(PendingAction, aid)
        assert row.status == "failed"
        assert "ValueError" in (row.result or {}).get("error", "")
        assert "kaboom" in (row.result or {}).get("error", "")
    finally:
        db.close()


# ─── unknown action name (registered then removed) ──────────


def test_decide_unknown_action_name_returns_500(
    client: TestClient, session_factory: sessionmaker, _restore_dispatch
):
    """A pending row whose action name is no longer registered → 500
    + row marked failed. Defensive path for handler-deregistration races."""
    uid, auth = _login_user(client, session_factory, email="ghosthandler@example.com")
    # Seed a pending row referencing an action that was NEVER registered.
    aid, _ = _seed_pending(
        session_factory, user_id=uid, action="never_registered"
    )

    r = client.post(
        f"/api/chat/actions/{aid}/decide",
        json={"decision": "approve"},
        headers=auth,
    )
    assert r.status_code == 500

    db = session_factory()
    try:
        row = db.get(PendingAction, aid)
        # Decide endpoint marks it failed + emits resolved before raising.
        assert row.status == "failed"
    finally:
        db.close()


# ─── reject of unknown action name should still succeed ────


def test_decide_reject_works_even_without_registered_handler(
    client: TestClient, session_factory: sessionmaker, _restore_dispatch
):
    """Reject doesn't dispatch — so missing handler shouldn't matter."""
    uid, auth = _login_user(client, session_factory, email="rejectnh@example.com")
    aid, _ = _seed_pending(
        session_factory, user_id=uid, action="totally_made_up"
    )

    r = client.post(
        f"/api/chat/actions/{aid}/decide",
        json={"decision": "reject", "notes": "nope"},
        headers=auth,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
