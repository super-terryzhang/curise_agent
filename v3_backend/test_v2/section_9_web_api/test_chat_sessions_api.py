"""Section 9 — Web API: `/api/chat/sessions/*` chat session CRUD + message.

测试目标：
    chat 路由是 AI 助手的入口：
    - POST /sessions 建会话
    - GET /sessions[/id] 列表 / 详情
    - DELETE /sessions/{id} 删会话
    - POST /sessions/{id}/messages 触发 agent 跑（LLM 全程 stub）
    - POST /sessions/{id}/cancel 中止
    - 跨用户访问必须 silently 404

为什么重要：
    chat 是用户和 v3 agent 唯一的对话面。一旦 send_message 出错或
    persist 漏掉 user/assistant 行，前端就找不到消息历史。
    LLM stub 是这类测试的标配——agent 跑的同步性由 conftest 的
    `_sync_job_runner` autouse fixture 保证。

设计方法：
    - Scripted LLM via `unittest.mock.patch("general_agent.llm.LLM.complete", ...)`
    - SynchronousRunner（conftest 默认）→ /messages POST 返回前 agent 已跑完。
    - 直接断 v3_chat_sessions / v3_chat_messages 表行数 + 角色。
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient
from openai.types.chat import ChatCompletionMessage
from sqlalchemy.orm import sessionmaker

from agent.storage.models import ChatMessage, ChatSession
from test_v2.fixtures.helpers import login, seed_user


# ─── Helpers ────────────────────────────────────────────────


def _scripted_llm_factory(reply_text: str = "OK"):
    """Build a fake LLM.complete that always returns a final text reply.

    Signature matches general-agent's LLM.complete (self + messages +
    optional tools/tool_choice + kwargs).
    """

    def fake_complete(self, messages, tools=None, tool_choice="auto", **kw):  # noqa: ARG001
        return ChatCompletionMessage(role="assistant", content=reply_text)

    return fake_complete


def _login_employee(client, session_factory, *, email="chat@example.com") -> dict:
    """Seed an employee + log in. Returns auth headers."""
    db = session_factory()
    try:
        seed_user(db, email=email, role="employee")
    finally:
        db.close()
    return login(client, email)


# ─── Session CRUD ───────────────────────────────────────────


def test_post_sessions_creates_chat_session(client, session_factory):
    """POST /chat/sessions returns id + title + initial counters."""
    auth = _login_employee(client, session_factory)
    r = client.post(
        "/api/chat/sessions",
        json={"title": "Test Session"},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"]
    assert body["title"] == "Test Session"
    assert body["total_prompt_tokens"] == 0


def test_post_sessions_persists_chat_session_row(client, session_factory):
    """The session id returned must correspond to a real DB row."""
    auth = _login_employee(client, session_factory)
    r = client.post(
        "/api/chat/sessions",
        json={"title": "Persist Me"},
        headers=auth,
    )
    sid = r.json()["id"]

    db = session_factory()
    try:
        row = db.get(ChatSession, sid)
        assert row is not None
        assert row.title == "Persist Me"
        assert row.user_id > 0
    finally:
        db.close()


def test_get_sessions_lists_my_sessions(client, session_factory):
    auth = _login_employee(client, session_factory)
    # Create two
    a = client.post("/api/chat/sessions", json={"title": "A"}, headers=auth).json()
    b = client.post("/api/chat/sessions", json={"title": "B"}, headers=auth).json()

    r = client.get("/api/chat/sessions", headers=auth)
    assert r.status_code == 200
    ids = {s["id"] for s in r.json()}
    assert a["id"] in ids and b["id"] in ids


def test_get_session_returns_session_and_messages(client, session_factory):
    """GET /sessions/{id} returns session + a `messages` list (possibly empty)."""
    auth = _login_employee(client, session_factory)
    sid = client.post(
        "/api/chat/sessions", json={"title": "with messages"}, headers=auth
    ).json()["id"]

    r = client.get(f"/api/chat/sessions/{sid}", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == sid
    assert "messages" in body
    assert isinstance(body["messages"], list)


def test_delete_session_removes_row(client, session_factory):
    auth = _login_employee(client, session_factory)
    sid = client.post(
        "/api/chat/sessions", json={"title": "Doomed"}, headers=auth
    ).json()["id"]

    r = client.delete(f"/api/chat/sessions/{sid}", headers=auth)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r = client.get(f"/api/chat/sessions/{sid}", headers=auth)
    assert r.status_code == 404


def test_cross_user_get_session_returns_404(client, session_factory):
    """User A's session must be invisible to user B (silent 404)."""
    auth_a = _login_employee(client, session_factory, email="a-chat@example.com")
    auth_b = _login_employee(client, session_factory, email="b-chat@example.com")

    sid = client.post(
        "/api/chat/sessions", json={"title": "private"}, headers=auth_a
    ).json()["id"]

    r = client.get(f"/api/chat/sessions/{sid}", headers=auth_b)
    assert r.status_code == 404


# ─── Messages + Agent (scripted LLM) ────────────────────────


def test_post_message_runs_agent_and_returns_ok(
    client: TestClient, session_factory: sessionmaker, monkeypatch
):
    """POST /sessions/{id}/messages with scripted LLM → 200, persists rows."""
    monkeypatch.setenv("STUB_LLM_KEY", "fake")
    monkeypatch.setattr("agent.runtime.llm.GEMINI_API_KEY_ENV", "STUB_LLM_KEY")

    auth = _login_employee(client, session_factory)
    sid = client.post(
        "/api/chat/sessions", json={"title": "msg-test"}, headers=auth
    ).json()["id"]

    fake = _scripted_llm_factory("hello back")
    with patch("general_agent.llm.LLM.complete", fake):
        r = client.post(
            f"/api/chat/sessions/{sid}/messages",
            json={"text": "hi"},
            headers=auth,
        )
    assert r.status_code == 200, r.text
    assert r.json()["session_id"] == sid


def test_post_message_persists_user_and_assistant_messages(
    client: TestClient, session_factory: sessionmaker, monkeypatch
):
    """The user prompt and the assistant reply must both land in
    v3_chat_messages."""
    monkeypatch.setenv("STUB_LLM_KEY", "fake")
    monkeypatch.setattr("agent.runtime.llm.GEMINI_API_KEY_ENV", "STUB_LLM_KEY")

    auth = _login_employee(client, session_factory)
    sid = client.post(
        "/api/chat/sessions", json={"title": "persist-test"}, headers=auth
    ).json()["id"]

    fake = _scripted_llm_factory("done")
    with patch("general_agent.llm.LLM.complete", fake):
        r = client.post(
            f"/api/chat/sessions/{sid}/messages",
            json={"text": "ping"},
            headers=auth,
        )
    assert r.status_code == 200

    db = session_factory()
    try:
        rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == sid)
            .order_by(ChatMessage.sequence)
            .all()
        )
        roles = [r.role for r in rows]
        assert "user" in roles, f"expected a user message, got {roles}"
        assert "assistant" in roles, f"expected an assistant message, got {roles}"
        assert len(rows) >= 2
    finally:
        db.close()


def test_post_message_cross_user_returns_404(client, session_factory, monkeypatch):
    monkeypatch.setenv("STUB_LLM_KEY", "fake")
    monkeypatch.setattr("agent.runtime.llm.GEMINI_API_KEY_ENV", "STUB_LLM_KEY")

    auth_a = _login_employee(client, session_factory, email="a-msg@example.com")
    auth_b = _login_employee(client, session_factory, email="b-msg@example.com")

    sid = client.post(
        "/api/chat/sessions", json={"title": "private"}, headers=auth_a
    ).json()["id"]

    r = client.post(
        f"/api/chat/sessions/{sid}/messages",
        json={"text": "leak me"},
        headers=auth_b,
    )
    assert r.status_code == 404


# ─── Cancel ─────────────────────────────────────────────────


def test_cancel_endpoint_returns_ok_for_own_session(client, session_factory):
    """POST /sessions/{id}/cancel returns ok=True; stream_found may be False
    if no run was active when the call landed (no-op cancel is still 200)."""
    auth = _login_employee(client, session_factory)
    sid = client.post(
        "/api/chat/sessions", json={"title": "cancel-test"}, headers=auth
    ).json()["id"]

    r = client.post(f"/api/chat/sessions/{sid}/cancel", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "stream_found" in body


def test_cancel_endpoint_404_for_other_users_session(client, session_factory):
    auth_a = _login_employee(client, session_factory, email="a-cancel@example.com")
    auth_b = _login_employee(client, session_factory, email="b-cancel@example.com")

    sid = client.post(
        "/api/chat/sessions", json={"title": "owned-by-a"}, headers=auth_a
    ).json()["id"]

    r = client.post(f"/api/chat/sessions/{sid}/cancel", headers=auth_b)
    assert r.status_code == 404


# ─── Sanity guard against accidental changes to envelope ────


def test_session_envelope_has_expected_fields(client, session_factory):
    """Frontend depends on these exact keys — guard against silent renames."""
    auth = _login_employee(client, session_factory)
    body = client.post(
        "/api/chat/sessions", json={"title": "envelope"}, headers=auth
    ).json()
    expected_keys = {
        "id",
        "user_id",
        "title",
        "status",
        "model",
        "total_prompt_tokens",
        "total_completion_tokens",
        "estimated_cost_usd",
        "created_at",
        "updated_at",
    }
    missing = expected_keys - body.keys()
    assert not missing, f"session envelope missing keys: {missing}"


# ─── Per-session model picker ──────────────────────────────


def test_list_models_returns_selectable_models(client, session_factory):
    """GET /chat/models is the static catalogue the UI picker reads.
    Must always include the prod default (Gemini) + Kimi fallback."""
    auth = _login_employee(client, session_factory, email="models@example.com")
    r = client.get("/api/chat/models", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "models" in body
    ids = {m["id"] for m in body["models"]}
    assert any("gemini" in i for i in ids), f"missing Gemini in {ids}"
    assert any("kimi" in i for i in ids), f"missing Kimi in {ids}"
    # Each entry has the fields the frontend renders.
    for m in body["models"]:
        assert {"id", "label", "provider"} <= m.keys()


def test_create_session_stores_model_preference(client, session_factory):
    """POST /sessions with `model` persists it; envelope echoes it back."""
    auth = _login_employee(client, session_factory, email="newmodel@example.com")
    r = client.post(
        "/api/chat/sessions",
        json={"title": "kimi-session", "model": "kimi-k2-0905-preview"},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "kimi-k2-0905-preview"

    sid = r.json()["id"]
    db = session_factory()
    try:
        row = db.get(ChatSession, sid)
        assert row.model == "kimi-k2-0905-preview"
    finally:
        db.close()


def test_patch_session_can_update_model(client, session_factory):
    """User switches model mid-conversation via PATCH; new value persists."""
    auth = _login_employee(client, session_factory, email="switch@example.com")
    sid = client.post(
        "/api/chat/sessions", json={"title": "switching"}, headers=auth
    ).json()["id"]

    r = client.patch(
        f"/api/chat/sessions/{sid}",
        json={"model": "kimi-k2-turbo-preview"},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "kimi-k2-turbo-preview"

    # Round-trip: GET /sessions/{id} reflects the change.
    body = client.get(f"/api/chat/sessions/{sid}", headers=auth).json()
    assert body["model"] == "kimi-k2-turbo-preview"


def test_patch_session_empty_string_clears_model(client, session_factory):
    """PATCH model='' resets to default (NULL → settings fallback)."""
    auth = _login_employee(client, session_factory, email="clear@example.com")
    sid = client.post(
        "/api/chat/sessions",
        json={"title": "clearing", "model": "kimi-k2-0905-preview"},
        headers=auth,
    ).json()["id"]

    r = client.patch(
        f"/api/chat/sessions/{sid}", json={"model": ""}, headers=auth
    )
    assert r.status_code == 200
    assert r.json()["model"] is None


def test_patch_session_cross_user_returns_404(client, session_factory):
    """Patching another user's session must silently 404."""
    auth_a = _login_employee(client, session_factory, email="a-patch@example.com")
    auth_b = _login_employee(client, session_factory, email="b-patch@example.com")
    sid = client.post(
        "/api/chat/sessions", json={"title": "owned-by-a"}, headers=auth_a
    ).json()["id"]

    r = client.patch(
        f"/api/chat/sessions/{sid}", json={"model": "kimi-k2"}, headers=auth_b
    )
    assert r.status_code == 404


# Silence linter about unused sessionmaker import in some setups
_ = sessionmaker
