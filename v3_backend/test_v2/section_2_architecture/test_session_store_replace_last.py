"""Section 2 — Architecture: SessionStore.replace_last contract.

测试目标：
    在 split-malformed-parallel-tool-call 路径里，core.py 先调
    `_persist_last` 把原始（拼接）assistant 消息存入 DB，再做 in-place
    split rewrite。如果不同步 DB，DB 里的 assistant.tool_calls 仍是
    单个拼接 id，而后续 tool result 行带的是 synthetic split ids ——
    前端 `stepById.get(callId)` 永远 miss，UI 显示"运行中 0/1 步"
    永远卡住。

为什么必须有：
    prod 2026-05-19 用户复现：问"东京+大阪+神户"3 件事，UI 卡 2 张
    orphan running 卡片。根因是 split 后没回写 DB，frontend 加载
    持久化历史时再现出这个 mismatch。replace_last 是核心修复点 ——
    没它的话 reload 后状态仍坏。
"""

from __future__ import annotations

from sqlalchemy import select

from agent.runtime.session_store import V3SessionStore
from agent.storage.models import ChatMessage
from domains.identity.models import User
from infrastructure.security import hash_password


def _user(db, *, email: str = "u@v") -> User:
    u = User(
        email=email,
        hashed_password=hash_password("p"),
        full_name="v",
        role="employee",
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_replace_last_overwrites_most_recent_message(db) -> None:
    """Append two messages, replace_last → only the second is rewritten."""
    u = _user(db, email="rl1@v")
    store = V3SessionStore(db, user_id=u.id)
    sid = store.create(model="gemini-3-flash-preview", title="t", system_prompt="")

    store.append(sid, {"role": "user", "content": "hi"})
    store.append(sid, {
        "role": "assistant",
        "tool_calls": [{
            "id": "concat-id",
            "type": "function",
            "function": {"name": "fooooofoo", "arguments": '{"a":1}{"a":2}'},
        }],
    })

    # Rewrite the second message with the split-and-fixed shape
    store.replace_last(sid, {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "concat-id-0",
                "type": "function",
                "function": {"name": "foo", "arguments": '{"a":1}'},
            },
            {
                "id": "concat-id-1",
                "type": "function",
                "function": {"name": "foo", "arguments": '{"a":2}'},
            },
        ],
    })

    loaded = store.load(sid)
    assert len(loaded) == 2, "should still have exactly 2 messages"
    assert loaded[0]["content"] == "hi", "first message must be untouched"
    tcs = loaded[1]["tool_calls"]
    assert len(tcs) == 2, "second message must have the split shape"
    assert tcs[0]["id"] == "concat-id-0"
    assert tcs[0]["function"]["name"] == "foo"
    assert tcs[1]["id"] == "concat-id-1"


def test_replace_last_on_empty_session_is_noop(db) -> None:
    """Calling replace_last when there are no messages must not raise."""
    u = _user(db, email="rl2@v")
    store = V3SessionStore(db, user_id=u.id)
    sid = store.create(model="g", title="t", system_prompt="")
    # Should be a no-op, not an exception
    store.replace_last(sid, {"role": "assistant", "content": "x"})
    assert store.load(sid) == []


def test_replace_last_respects_user_isolation(db) -> None:
    """Cross-user replace_last must raise PermissionError so a malicious
    caller can't rewrite another user's session."""
    u1 = _user(db, email="rl3a@v")
    u2 = _user(db, email="rl3b@v")
    store_owner = V3SessionStore(db, user_id=u1.id)
    sid = store_owner.create(model="g", title="t", system_prompt="")
    store_owner.append(sid, {"role": "user", "content": "secret"})

    store_attacker = V3SessionStore(db, user_id=u2.id)
    try:
        store_attacker.replace_last(sid, {"role": "assistant", "content": "hijacked"})
    except PermissionError:
        pass
    else:
        raise AssertionError("replace_last must reject cross-user write")

    # Owner's session is intact
    loaded = store_owner.load(sid)
    assert loaded[0]["content"] == "secret"


def test_replace_last_updates_chatmessage_parts(db) -> None:
    """Direct DB inspection: the underlying ChatMessage.parts JSON must
    reflect the new shape (not still carry the pre-split tool_call_ids)."""
    u = _user(db, email="rl4@v")
    store = V3SessionStore(db, user_id=u.id)
    sid = store.create(model="g", title="t", system_prompt="")
    store.append(sid, {
        "role": "assistant",
        "tool_calls": [{
            "id": "OLD",
            "type": "function",
            "function": {"name": "x", "arguments": "{}"},
        }],
    })
    store.replace_last(sid, {
        "role": "assistant",
        "tool_calls": [{
            "id": "NEW",
            "type": "function",
            "function": {"name": "x", "arguments": "{}"},
        }],
    })

    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == sid)
        .order_by(ChatMessage.sequence.desc())
        .limit(1)
    )
    row = db.execute(stmt).scalar_one()
    parts = row.parts or []
    assert parts, "parts JSON must not be empty after replace_last"
    payload = parts[0].get("data") or {}
    tool_calls = payload.get("tool_calls") or []
    assert tool_calls and tool_calls[0]["id"] == "NEW", (
        "DB row must reflect the rewritten tool_call_id — without this "
        "the frontend stepById matching breaks after session reload"
    )
