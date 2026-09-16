"""Section 9 — Web API: `/api/chat/actions/{id}/decide` triggers a
synthetic follow-up agent turn after dispatch.

测试目标：
    用户点 ApprovalCard 的「确认执行」按钮 → POST /actions/N/decide →
    后端 (a) 真跑 dispatch (b) **触发一次 agent.run() 把结果反馈成
    follow-up assistant 消息**。这是 Interrupt+Resume 模式的 closure
    端 —— 没有它，chat 在用户点完按钮后就静止，用户得追问"完成了
    吗"。

为什么重要：
    Prod 2026-05-14 真实事故：用户点 approve 后 agent 不主动反馈，
    用户来回追问"操作完成了吗" agent 才间接报告。这条测试 pin 住
    "decide → synthetic user msg → assistant follow-up" 三步闭环。

设计方法：
    - LLM stub (`_scripted_llm_factory`) 模拟 follow-up agent 的回复
    - 注册一个 fake action_spec dispatch
    - POST /decide approve → SynchronousRunner 跑完 followup job
    - 直接查 v3_chat_messages 验证新行
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from openai.types.chat import ChatCompletionMessage
from sqlalchemy.orm import sessionmaker

from agent.runtime.approvals import ACTION_DISPATCH, ActionSpec, register_action
from agent.storage.models import ChatMessage, PendingAction
from test_v2.fixtures.helpers import login, seed_user


# Re-use the chat tests' stub LLM factory (kept inline so this file is
# standalone and runnable in isolation).
def _scripted_llm(reply_text: str):
    def fake_complete(self, messages, tools=None, tool_choice="auto", **kw):  # noqa: ARG001
        return ChatCompletionMessage(role="assistant", content=reply_text)

    return fake_complete


_FAKE_ACTION_NAME = "test_followup_action"


@pytest.fixture
def _fake_dispatch_action():
    """Register a test-only ActionSpec we can dispatch through decide.
    Cleans up after test so other tests don't see the registration."""

    def _do(deps, target_id, payload):  # noqa: ARG001
        return {"created": 7, "echo": payload.get("note")}

    spec = ActionSpec(
        name=_FAKE_ACTION_NAME,
        target_kind="upload_batch",
        description="test-only action",
        dispatch=_do,
    )
    register_action(spec)
    yield
    ACTION_DISPATCH.pop(_FAKE_ACTION_NAME, None)


def _login_employee(client, session_factory) -> tuple[dict, int]:
    """Seed an employee + log in. Returns (auth_headers, user_id)."""
    db = session_factory()
    try:
        u = seed_user(db, email="dec@example.com", role="employee")
        uid = u.id
    finally:
        db.close()
    return login(client, "dec@example.com"), uid


def _make_session(client, auth, title="approval-test") -> str:
    r = client.post("/api/chat/sessions", json={"title": title}, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _make_pending(
    session_factory, session_id: str, user_id: int, *, action: str
) -> int:
    db = session_factory()
    try:
        row = PendingAction(
            session_id=session_id,
            user_id=user_id,
            action=action,
            target_kind="upload_batch",
            target_id=None,
            payload={"note": "hello"},
            summary="提交批量上传 (test)",
            status="pending",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


# ─── Happy path: approve dispatches AND triggers followup ──────


@pytest.mark.asyncio
async def test_approve_triggers_followup_agent_turn(
    client,
    session_factory: sessionmaker,
    monkeypatch,
    _fake_dispatch_action,
):
    """Approve decision → dispatch runs → synthetic user message + agent
    follow-up message persist into v3_chat_messages."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-no-network")

    auth, uid = _login_employee(client, session_factory)
    sid = _make_session(client, auth)
    action_id = _make_pending(
        session_factory, sid, uid, action=_FAKE_ACTION_NAME
    )

    fake = _scripted_llm("✅ 已成功完成（mock 回复）")
    with patch("general_agent.llm.LLM.complete", fake):
        r = client.post(
            f"/api/chat/actions/{action_id}/decide",
            json={"decision": "approve"},
            headers=auth,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["result"]["created"] == 7  # dispatch ran

    # Verify chat_messages got the synthetic user + assistant followup.
    db = session_factory()
    try:
        rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == sid)
            .order_by(ChatMessage.sequence)
            .all()
        )
    finally:
        db.close()

    user_rows = [r for r in rows if r.role == "user"]
    assistant_rows = [r for r in rows if r.role == "assistant"]

    # Synthetic user message was persisted with the marker.
    synth = [
        r
        for r in user_rows
        if "[__APPROVAL_FOLLOWUP__]" in str((r.parts or [{}])[0].get("data", {}).get("content", ""))
    ]
    assert len(synth) == 1, (
        f"expected one synthetic user message tagged "
        f"'[__APPROVAL_FOLLOWUP__]', got: {[r.role for r in rows]}"
    )

    # An assistant message followed it.
    assert len(assistant_rows) >= 1


@pytest.mark.asyncio
async def test_reject_also_triggers_followup_with_cancellation_phrasing(
    client,
    session_factory: sessionmaker,
    monkeypatch,
    _fake_dispatch_action,
):
    """Reject path: agent should announce cancellation, not dispatch."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-no-network")

    auth, uid = _login_employee(client, session_factory)
    sid = _make_session(client, auth)
    action_id = _make_pending(
        session_factory, sid, uid, action=_FAKE_ACTION_NAME
    )

    fake = _scripted_llm("已取消该操作（mock 回复）")
    with patch("general_agent.llm.LLM.complete", fake):
        r = client.post(
            f"/api/chat/actions/{action_id}/decide",
            json={"decision": "reject", "notes": "改主意了"},
            headers=auth,
        )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"

    db = session_factory()
    try:
        rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == sid)
            .order_by(ChatMessage.sequence)
            .all()
        )
    finally:
        db.close()

    synth_user = [
        r
        for r in rows
        if r.role == "user"
        and "[__APPROVAL_FOLLOWUP__]"
        in str((r.parts or [{}])[0].get("data", {}).get("content", ""))
    ]
    # The synthetic input was sent, telling the agent the action was rejected.
    assert len(synth_user) == 1
    content = str(synth_user[0].parts[0]["data"]["content"])
    assert "rejected" in content or "被rejected" in content

    # And at least one assistant message followed (the cancellation note).
    assert any(r.role == "assistant" for r in rows)


@pytest.mark.asyncio
async def test_dispatch_failure_does_not_trigger_followup(
    client,
    session_factory: sessionmaker,
    monkeypatch,
):
    """If dispatch raises, we mark_decided(failed) but don't trigger an
    agent followup — the HTTP 500 carries the error back to the UI which
    already handles it via a toast. Triggering followup on failures
    would mean: agent says "operation done" right after we just told the
    UI it errored. Asymmetric on purpose: ✓ success/reject get follow-up,
    ✗ system errors don't."""

    def _boom(deps, target_id, payload):  # noqa: ARG001
        raise RuntimeError("boom")

    spec = ActionSpec(
        name="boom_action",
        target_kind="x",
        description="fails",
        dispatch=_boom,
    )
    register_action(spec)

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-no-network")
    try:
        auth, uid = _login_employee(client, session_factory)
        sid = _make_session(client, auth)
        action_id = _make_pending(
            session_factory, sid, uid, action="boom_action"
        )

        fake = _scripted_llm("(should not be called)")
        with patch("general_agent.llm.LLM.complete", fake):
            r = client.post(
                f"/api/chat/actions/{action_id}/decide",
                json={"decision": "approve"},
                headers=auth,
            )
        assert r.status_code == 500

        # No follow-up persisted.
        db = session_factory()
        try:
            rows = (
                db.query(ChatMessage)
                .filter(ChatMessage.session_id == sid)
                .all()
            )
        finally:
            db.close()
        synth_count = sum(
            1
            for r in rows
            if r.role == "user"
            and "[__APPROVAL_FOLLOWUP__]"
            in str((r.parts or [{}])[0].get("data", {}).get("content", ""))
        )
        assert synth_count == 0
    finally:
        ACTION_DISPATCH.pop("boom_action", None)
