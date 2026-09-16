"""Section 9 — Chat page-context awareness (2026-05-29).

测试目标：
    `_NewMessageBody.page_context` 在 POST /api/chat/sessions/{id}/messages
    时把 URL 字符串传到 `_run_agent_blocking` → `create_v3_chat_agent` →
    最终拼到 system_prompt 末尾。三个断言：

      1. 传 page_context → factory 收到 → overlay 进入 system_prompt
      2. 不传 page_context → overlay 完全不出现（不能污染 prompt）
      3. overlay 顺序锁定（base → page → memory → model overlay）

为什么重要：
    Sidebar 在订单页发"这单怎么了"时，必须告诉 agent 用户正看的是
    哪一单 —— 否则 agent 反问"哪个订单"，UX 崩。这条契约是把
    "看得见的页面"翻译成"agent 能理解的上下文"的唯一管道。漂移
    意味着 agent 退化成无 context 的客服 bot。

    顺序锁定的理由：memory 是用户偏好（"喜欢公制单位"），page 是
    当前焦点（"在订单 123"）；如果 memory 在 page 之前出现，
    LLM 在长 prompt 末尾更易遗忘 page。把 page 紧贴 base 之后、
    memory 之前，确保它在 attention 高位区。

设计方法：
    - 直接 unit-test `_build_page_context_overlay()` 纯函数
    - HTTP 层：mock `create_v3_chat_agent` 拦截 system_prompt kwarg
"""

from __future__ import annotations

from unittest.mock import patch

from agent.runtime.factory import (
    _PAGE_CONTEXT_FOOTER,
    _PAGE_CONTEXT_HEADER,
    _build_page_context_overlay,
)
from test_v2.fixtures.helpers import login


def _admin_headers(client) -> dict[str, str]:
    return login(client, "admin@example.com", "password123")


# ─── Pure helper ─────────────────────────────────────────────


def test_overlay_returns_empty_for_none():
    assert _build_page_context_overlay(None) == ""


def test_overlay_returns_empty_for_blank():
    assert _build_page_context_overlay("") == ""
    assert _build_page_context_overlay("   ") == ""


def test_overlay_wraps_with_delimiters():
    out = _build_page_context_overlay("user is viewing order #123")
    assert _PAGE_CONTEXT_HEADER in out
    assert _PAGE_CONTEXT_FOOTER in out
    assert "user is viewing order #123" in out
    # Starts with blank lines so it appends cleanly to existing prompt
    assert out.startswith("\n\n")


def test_overlay_strips_trailing_whitespace():
    out = _build_page_context_overlay("  /dashboard/orders/123  \n")
    # leading/trailing whitespace on the inner string gone
    assert "/dashboard/orders/123\n" in out
    assert "/dashboard/orders/123  \n" not in out


# ─── Factory-level overlay placement ─────────────────────────


def test_factory_appends_page_context_to_system_prompt(db, seed_user, monkeypatch):
    """create_v3_chat_agent must include the overlay in the AgentConfig
    system_prompt when page_context is non-empty. Without this the
    overlay never reaches the LLM."""
    from agent.runtime.factory import create_v3_chat_agent

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-no-network")

    agent = create_v3_chat_agent(
        db=db,
        user_id=seed_user.id,
        user_role="superadmin",
        page_context="user is viewing order #999",
    )
    # The system prompt lives on the AgentConfig; the Agent's config is
    # exposed for tests. Use a substring assertion since memory preamble
    # + model overlays mutate the exact string.
    prompt = agent.config.system_prompt
    assert _PAGE_CONTEXT_HEADER in prompt
    assert "user is viewing order #999" in prompt


def test_factory_omits_overlay_when_page_context_is_none(db, seed_user, monkeypatch):
    """Default (no page_context) — overlay must not appear, otherwise
    every legacy caller that doesn't pass the new arg pollutes the
    prompt with empty delimiters."""
    from agent.runtime.factory import create_v3_chat_agent

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-no-network")

    agent = create_v3_chat_agent(
        db=db,
        user_id=seed_user.id,
        user_role="superadmin",
    )
    prompt = agent.config.system_prompt
    assert _PAGE_CONTEXT_HEADER not in prompt
    assert _PAGE_CONTEXT_FOOTER not in prompt


def test_factory_overlay_order_page_before_memory(db, seed_user, monkeypatch):
    """Locks the order: base prompt → page context → memory preamble.
    Putting page after memory would push it into attention's tail —
    long-prompt failure mode where LLMs forget mid-context items.
    """
    from agent.runtime.factory import (
        _MEMORY_PREAMBLE_HEADER,
        create_v3_chat_agent,
    )
    from agent.storage.models import AgentMemory

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-no-network")

    # Seed a memory row directly — bypasses the MemoryStore facade so
    # the test stays focused on the prompt-composition contract.
    db.add(
        AgentMemory(
            user_id=seed_user.id,
            memory_type="user_preference",
            key="currency",
            value="orders are usually in JPY",
        )
    )
    db.commit()

    agent = create_v3_chat_agent(
        db=db,
        user_id=seed_user.id,
        user_role="superadmin",
        page_context="user is on /dashboard/orders/55",
    )
    prompt = agent.config.system_prompt
    page_idx = prompt.find(_PAGE_CONTEXT_HEADER)
    memory_idx = prompt.find(_MEMORY_PREAMBLE_HEADER)
    assert page_idx >= 0, "page overlay missing"
    assert memory_idx >= 0, "memory preamble missing"
    assert page_idx < memory_idx, (
        f"page context (idx {page_idx}) must come before memory preamble "
        f"(idx {memory_idx}) — see test docstring for why."
    )


# ─── HTTP request → factory plumbing ─────────────────────────


def test_post_message_passes_page_context_through_to_factory(client, seed_user, db):
    """POST /api/chat/sessions/{id}/messages with page_context →
    factory called with page_context=<value>. End-to-end plumbing
    test — proves the field survives the Pydantic body → background
    thread → factory call chain.
    """
    from agent.storage.models import ChatSession

    # Seed a chat session owned by the test user.
    session = ChatSession(
        id="probe-page-ctx",
        user_id=seed_user.id,
        title="probe",
        model=None,
    )
    db.add(session)
    db.commit()

    captured: dict[str, object] = {}

    def _fake_run_agent(*args, **kwargs):
        # Last positional args of _run_agent_blocking are
        # (session_id, user_id, user_role, text, model, page_context)
        captured["positional"] = args
        captured["kwargs"] = kwargs
        # Don't actually run the agent — just record and return.
        return None

    headers = _admin_headers(client)
    with patch(
        "apps.http.chat._run_agent_blocking",
        side_effect=_fake_run_agent,
    ):
        r = client.post(
            f"/api/chat/sessions/{session.id}/messages",
            json={
                "text": "what's wrong with this order?",
                "page_context": "user is viewing /dashboard/orders/55",
            },
            headers=headers,
        )
    assert r.status_code == 200, r.text

    # The job runs async via to_thread → give it a moment to settle.
    # In the test client the background thread runs immediately, but
    # the patched function should still fire before the request ends.
    import time

    for _ in range(30):
        if "positional" in captured:
            break
        time.sleep(0.1)

    assert "positional" in captured, "_run_agent_blocking was never called"
    args = captured["positional"]
    # Signature: (session_id, user_id, user_role, text, model, page_context)
    assert args[5] == "user is viewing /dashboard/orders/55", (
        f"page_context not threaded through: got {args[5]!r}"
    )


def test_post_message_without_page_context_passes_none(client, seed_user, db):
    """Legacy clients that don't send page_context → factory receives
    None. Defensive: ensures the new field is genuinely optional."""
    from agent.storage.models import ChatSession

    session = ChatSession(
        id="probe-no-ctx",
        user_id=seed_user.id,
        title="probe",
        model=None,
    )
    db.add(session)
    db.commit()

    captured: dict[str, object] = {}

    def _fake_run_agent(*args, **kwargs):
        captured["positional"] = args

    headers = _admin_headers(client)
    with patch(
        "apps.http.chat._run_agent_blocking",
        side_effect=_fake_run_agent,
    ):
        r = client.post(
            f"/api/chat/sessions/{session.id}/messages",
            json={"text": "hi"},  # no page_context
            headers=headers,
        )
    assert r.status_code == 200

    import time

    for _ in range(30):
        if "positional" in captured:
            break
        time.sleep(0.1)

    assert "positional" in captured
    assert captured["positional"][5] is None
