"""Section 9 — Web API: GET /sessions/{id} rebuilds artifacts (v42+).

测试目标：
    prod 2026-05-20 bug：刷新页面后 generic_table / upload_diff_viewer
    artifact 面板全部消失。根因——artifact 数据走 SSE 事件实时推到前端，
    持久化只有 assistant 消息里的 tool_call arguments，前端 loadSession
    时只 setMessages，没有解析 arguments 重建 artifacts。

    v42 修复：GET /sessions/{id} 扫消息历史，从 `present_artifact` 的
    tool_call arguments 解析出来，返回 `artifacts[]` 字段。前端 rehydrate
    后刷新就能恢复面板。

    本测试 pin 住后端契约——不依赖前端实现，纯 HTTP 层验证返回结构。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from agent.storage.models import ChatMessage, ChatSession
from test_v2.fixtures.helpers import login, seed_user


def _seed_session(session_factory: sessionmaker, *, user_id: int, sid: str) -> None:
    db = session_factory()
    try:
        if db.get(ChatSession, sid) is None:
            db.add(ChatSession(id=sid, user_id=user_id, title="t"))
            db.commit()
    finally:
        db.close()


def _seed_assistant_with_artifact(
    session_factory: sessionmaker,
    *,
    sid: str,
    sequence: int,
    tool_call_id: str,
    artifact_args: dict[str, Any],
) -> None:
    """Persist an assistant message with a `present_artifact` tool_call,
    mirroring what core.py would have written during a live agent run."""
    db = session_factory()
    try:
        db.add(ChatMessage(
            session_id=sid,
            sequence=sequence,
            role="assistant",
            parts=[{
                "type": "raw",
                "schema": "openai-chat-completion",
                "data": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": "present_artifact",
                            "arguments": json.dumps(artifact_args, ensure_ascii=False),
                        },
                    }],
                },
            }],
        ))
        db.commit()
    finally:
        db.close()


def _seed_tool_result(
    session_factory: sessionmaker,
    *,
    sid: str,
    sequence: int,
    tool_call_id: str,
    content: str,
) -> None:
    db = session_factory()
    try:
        db.add(ChatMessage(
            session_id=sid,
            sequence=sequence,
            role="tool",
            parts=[{
                "type": "raw",
                "schema": "openai-chat-completion",
                "data": {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": "present_artifact",
                    "content": content,
                },
            }],
        ))
        db.commit()
    finally:
        db.close()


def _login(client: TestClient, session_factory: sessionmaker,
           *, email: str) -> tuple[int, dict[str, str]]:
    db = session_factory()
    try:
        u = seed_user(db, email=email, role="employee")
        uid = u.id
    finally:
        db.close()
    return uid, login(client, email)


# ─── generic_table rehydration ──────────────────────────────


def test_get_session_rehydrates_generic_table_artifact(
    client: TestClient, session_factory: sessionmaker
):
    """A successful present_artifact call should reappear in the
    `artifacts[]` payload with columns/rows intact after page reload."""
    uid, auth = _login(client, session_factory, email="rehydrate1@v")
    sid = f"sess-rehydrate-gt-{uid}"
    _seed_session(session_factory, user_id=uid, sid=sid)

    artifact_args = {
        "component": "generic_table",
        "narration": "Tokyo hourly weather",
        "data": json.dumps({
            "title": "Tokyo 24h",
            "columns": [
                {"key": "hour", "label": "时间"},
                {"key": "temp", "label": "气温"},
            ],
            "rows": [
                {"hour": "14:00", "temp": "26°C"},
                {"hour": "15:00", "temp": "27°C"},
            ],
        }),
    }
    _seed_assistant_with_artifact(
        session_factory, sid=sid, sequence=0,
        tool_call_id="tc-1", artifact_args=artifact_args,
    )
    _seed_tool_result(
        session_factory, sid=sid, sequence=1,
        tool_call_id="tc-1",
        content="Artifact dispatched: component='generic_table', data_keys=['columns','rows','title'], narration='...'",
    )

    r = client.get(f"/api/chat/sessions/{sid}", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()

    arts = body.get("artifacts") or []
    assert len(arts) == 1, f"expected 1 artifact in response, got {arts}"
    art = arts[0]
    assert art["component"] == "generic_table"
    assert art["narration"] == "Tokyo hourly weather"
    assert art["session_id"] == sid
    # The `data` payload survives — frontend can render the table.
    data = art["data"]
    assert data["title"] == "Tokyo 24h"
    assert len(data["columns"]) == 2
    assert data["columns"][0]["key"] == "hour"
    assert len(data["rows"]) == 2
    assert data["rows"][0]["hour"] == "14:00"


def test_get_session_rehydrates_upload_diff_viewer_artifact(
    client: TestClient, session_factory: sessionmaker
):
    """upload_diff_viewer carries only a reference (batch_id). The
    backend's rehydration must surface that same reference; the frontend
    re-fetches the actual diff from the artifact REST endpoint."""
    uid, auth = _login(client, session_factory, email="rehydrate2@v")
    sid = f"sess-rehydrate-udv-{uid}"
    _seed_session(session_factory, user_id=uid, sid=sid)

    artifact_args = {
        "component": "upload_diff_viewer",
        "narration": "Batch 12 preview",
        "data": json.dumps({"batch_id": 12, "view": {"mode": "table"}}),
    }
    _seed_assistant_with_artifact(
        session_factory, sid=sid, sequence=0,
        tool_call_id="tc-batch", artifact_args=artifact_args,
    )
    _seed_tool_result(
        session_factory, sid=sid, sequence=1,
        tool_call_id="tc-batch",
        content="Artifact dispatched: component='upload_diff_viewer', data_keys=['batch_id','view'], narration='...'",
    )

    r = client.get(f"/api/chat/sessions/{sid}", headers=auth)
    arts = r.json().get("artifacts") or []
    assert len(arts) == 1
    assert arts[0]["component"] == "upload_diff_viewer"
    assert arts[0]["data"] == {"batch_id": 12, "view": {"mode": "table"}}


# ─── Failed artifacts must NOT be rehydrated ────────────────


def test_get_session_skips_failed_present_artifact_calls(
    client: TestClient, session_factory: sessionmaker
):
    """If present_artifact validation failed (tool_result starts with
    'Error:'), the frontend never showed the panel — reload must not
    resurrect it. Otherwise the user sees a panel they already moved
    past."""
    uid, auth = _login(client, session_factory, email="rehydrate3@v")
    sid = f"sess-rehydrate-fail-{uid}"
    _seed_session(session_factory, user_id=uid, sid=sid)

    # First call: failed (validator rejected, e.g. missing key)
    _seed_assistant_with_artifact(
        session_factory, sid=sid, sequence=0,
        tool_call_id="tc-bad",
        artifact_args={
            "component": "generic_table",
            "narration": "x",
            "data": json.dumps({"columns": [], "rows": []}),
        },
    )
    _seed_tool_result(
        session_factory, sid=sid, sequence=1,
        tool_call_id="tc-bad",
        content="Error: ⚠️ Artifact NOT dispatched for component 'generic_table'. The user will see NO panel until you fix and retry.",
    )
    # Second call: succeeded (retry after error)
    _seed_assistant_with_artifact(
        session_factory, sid=sid, sequence=2,
        tool_call_id="tc-ok",
        artifact_args={
            "component": "generic_table",
            "narration": "Fixed",
            "data": json.dumps({"title": "X", "columns": [], "rows": []}),
        },
    )
    _seed_tool_result(
        session_factory, sid=sid, sequence=3,
        tool_call_id="tc-ok",
        content="Artifact dispatched: component='generic_table', data_keys=['columns','rows','title'], narration='Fixed'",
    )

    r = client.get(f"/api/chat/sessions/{sid}", headers=auth)
    arts = r.json().get("artifacts") or []
    assert len(arts) == 1, (
        f"only the successful artifact should rehydrate, got {len(arts)}"
    )
    assert arts[0]["narration"] == "Fixed"


# ─── Multi-artifact ordering ────────────────────────────────


def test_get_session_returns_multiple_artifacts_in_order(
    client: TestClient, session_factory: sessionmaker
):
    """Multiple artifacts in one session must come back in message-
    sequence order so the frontend renders them top-to-bottom matching
    the conversation flow."""
    uid, auth = _login(client, session_factory, email="rehydrate4@v")
    sid = f"sess-rehydrate-multi-{uid}"
    _seed_session(session_factory, user_id=uid, sid=sid)

    for i, label in enumerate(["first", "second", "third"]):
        _seed_assistant_with_artifact(
            session_factory, sid=sid, sequence=i * 2,
            tool_call_id=f"tc-{i}",
            artifact_args={
                "component": "generic_table",
                "narration": label,
                "data": json.dumps({"title": label, "columns": [], "rows": []}),
            },
        )
        _seed_tool_result(
            session_factory, sid=sid, sequence=i * 2 + 1,
            tool_call_id=f"tc-{i}",
            content=f"Artifact dispatched: ...{label}",
        )

    r = client.get(f"/api/chat/sessions/{sid}", headers=auth)
    arts = r.json().get("artifacts") or []
    narrations = [a["narration"] for a in arts]
    assert narrations == ["first", "second", "third"]
    # Monotonic increasing ids so frontend doesn't collide with its
    # SSE-side artifactSeqRef counter.
    assert [a["id"] for a in arts] == sorted(a["id"] for a in arts)
