"""Section E2E — HITL (Human-In-The-Loop) full loop.

测试目标：
    用户触发 propose_action（agent 内部）→ 看到 PendingAction → 在 UI 上点
    approve → 后端 dispatch handler 真的执行 → 数据库真的改变。

为什么重要：
    propose_action 是 v3 所有"危险/财务/批量"操作的唯一通道（删订单、改产品
    价格、更新供应商分类等）。如果 approve 按钮假装生效（mark "approved" 但
    不 dispatch），用户以为东西改了，实际没改 —— 数据一致性事故。

设计方法：
    1. 注册一个测试 ActionSpec（"e2e_test_action"）做 sentinel dispatch。
    2. 直接 seed 一条 PendingAction 行（模拟 agent 调用 propose）。
    3. POST /api/chat/actions/{id}/decide approve → 验证 dispatch 调用 +
       DB 状态 + 响应体。
    4. 也验证 reject 路径 + 跨用户 404 + 重复 decide 409。
"""

from __future__ import annotations

import pytest

from agent.runtime.approvals import ACTION_DISPATCH, ActionSpec, register_action
from agent.storage.models import ChatSession, PendingAction
from test_v2.fixtures.helpers import login, seed_user


# ─── Test action registration ────────────────────────────────


@pytest.fixture
def test_action_dispatched(monkeypatch):
    """Register a sentinel ActionSpec. Tests assert this dispatcher was
    invoked by reading its captured calls."""
    calls: list[dict] = []

    def _dispatch(deps, target_id, payload):
        calls.append({
            "user_id": deps.user_id,
            "target_id": target_id,
            "payload": payload,
        })
        return {"executed": True, "target_id": target_id, "payload": payload}

    spec = ActionSpec(
        name="e2e_test_action",
        target_kind="test",
        description="E2E HITL test sentinel",
        dispatch=_dispatch,
    )
    register_action(spec)
    yield calls
    # Clean up to keep ACTION_DISPATCH from leaking across tests
    ACTION_DISPATCH.pop("e2e_test_action", None)


@pytest.fixture
def session_and_pending(db, client, employee_auth, test_action_dispatched):
    """Seed a ChatSession + a PendingAction for the employee."""
    from domains.identity.models import User

    user = db.query(User).filter_by(email="e2e_emp@x.test").one()

    session = ChatSession(
        id="e2e-session-1",
        user_id=user.id,
        title="HITL E2E session",
    )
    db.add(session)
    db.commit()

    action = PendingAction(
        session_id=session.id,
        user_id=user.id,
        action="e2e_test_action",
        target_kind="test",
        target_id=12345,
        payload={"name": "hello", "amount": 99},
        summary="E2E sentinel approval",
        status="pending",
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return session, action


# ─── 1. Approve dispatches + DB updates ─────────────────────


def test_approve_dispatches_handler_and_marks_approved(
    client, employee_auth, session_and_pending, test_action_dispatched, db
):
    """Happy path: approve → handler called → DB status='approved' + result stored."""
    session, action = session_and_pending

    r = client.post(
        f"/api/chat/actions/{action.id}/decide",
        json={"decision": "approve"},
        headers=employee_auth,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["result"]["executed"] is True
    assert body["result"]["target_id"] == 12345
    assert body["result"]["payload"] == {"name": "hello", "amount": 99}

    # Dispatcher was called exactly once with the right args
    assert len(test_action_dispatched) == 1
    assert test_action_dispatched[0]["target_id"] == 12345

    # DB row status updated
    db.refresh(action)
    assert action.status == "approved"
    assert action.result["executed"] is True


# ─── 2. Reject does NOT dispatch ────────────────────────────


def test_reject_marks_rejected_and_does_not_dispatch(
    client, employee_auth, session_and_pending, test_action_dispatched, db
):
    session, action = session_and_pending

    r = client.post(
        f"/api/chat/actions/{action.id}/decide",
        json={"decision": "reject", "notes": "no thanks"},
        headers=employee_auth,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"

    # Critical: handler must NOT have been called
    assert len(test_action_dispatched) == 0

    db.refresh(action)
    assert action.status == "rejected"


# ─── 3. Cross-user blocked ──────────────────────────────────


def test_other_user_cannot_decide_someone_elses_action(
    client, employee_auth, session_and_pending, test_action_dispatched, db
):
    """Same as test_other_user_cannot_decide — Employee B can't approve
    Employee A's pending action. Returns 404 (don't leak existence)."""
    session, action = session_and_pending

    seed_user(db, email="other_e2e@x.test", role="employee", password="password123")
    other_auth = login(client, "other_e2e@x.test")

    r = client.post(
        f"/api/chat/actions/{action.id}/decide",
        json={"decision": "approve"},
        headers=other_auth,
    )
    assert r.status_code == 404, r.text
    assert len(test_action_dispatched) == 0  # handler not invoked


# ─── 4. Double-decide rejected ───────────────────────────────


def test_already_decided_action_returns_409(
    client, employee_auth, session_and_pending, db
):
    """First approve succeeds; second decide returns 409 Conflict."""
    session, action = session_and_pending

    r1 = client.post(
        f"/api/chat/actions/{action.id}/decide",
        json={"decision": "approve"},
        headers=employee_auth,
    )
    assert r1.status_code == 200

    r2 = client.post(
        f"/api/chat/actions/{action.id}/decide",
        json={"decision": "reject"},
        headers=employee_auth,
    )
    assert r2.status_code == 409


# ─── 5. Unknown action_id → 404 ─────────────────────────────


def test_decide_with_unknown_action_id_returns_404(client, employee_auth):
    r = client.post(
        "/api/chat/actions/999999/decide",
        json={"decision": "approve"},
        headers=employee_auth,
    )
    assert r.status_code == 404


# ─── 6. Handler raises → 500 + status=failed ────────────────


def test_handler_exception_marks_failed_and_returns_500(
    client, db, employee_auth, monkeypatch
):
    """If the registered dispatcher raises, action is marked 'failed' (not
    'approved') and the user gets 500. The user's data is NOT silently
    half-updated."""
    from domains.identity.models import User

    user = db.query(User).filter_by(email="e2e_emp@x.test").one()

    def _explode(deps, target_id, payload):
        raise RuntimeError("simulated downstream failure")

    spec = ActionSpec(
        name="e2e_failing_action",
        target_kind="test",
        description="E2E failing sentinel",
        dispatch=_explode,
    )
    register_action(spec)
    try:
        session = ChatSession(id="e2e-fail-session", user_id=user.id, title="x")
        db.add(session)
        action = PendingAction(
            session_id=session.id,
            user_id=user.id,
            action="e2e_failing_action",
            target_kind="test",
            target_id=1,
            payload={},
            summary="fail test",
            status="pending",
        )
        db.add(action)
        db.commit()
        db.refresh(action)

        r = client.post(
            f"/api/chat/actions/{action.id}/decide",
            json={"decision": "approve"},
            headers=employee_auth,
        )
        assert r.status_code == 500
        db.refresh(action)
        assert action.status == "failed"
        assert "simulated downstream failure" in (
            (action.result or {}).get("error", "")
        )
    finally:
        ACTION_DISPATCH.pop("e2e_failing_action", None)
