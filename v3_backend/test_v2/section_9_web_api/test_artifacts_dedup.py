"""Section 9 — Web API: artifact dedup in GET /sessions/{id}.

测试目标:
    Agents (especially after a retry) sometimes call `present_artifact`
    twice with identical (component, data) payloads. Before dedup, the
    UI would render N identical tabs in the right pane — pure noise.

    `_extract_artifacts_from_messages` collapses such duplicates on
    (component, sorted-JSON data) hash, keeping the LATEST occurrence
    (in case a subsequent dispatch carries new narration/timestamp).
    Sequential ids are reassigned post-dedup so "3 / 5" position
    counters in the UI stay sensible.

    Narration is INTENTIONALLY NOT part of the dedup key — agents often
    rephrase the narration ("Tokyo weather" → "Tokyo hourly weather")
    while the underlying table is identical. Keying on narration would
    leak those typo-variants as visible tabs.

测试维度:
    A. 同 component + 同 data, 同 narration → 1 个 artifact
    B. 同 component + 同 data, 不同 narration → 1 个 artifact (使用最后)
    C. 同 component + 不同 data → 2 个 artifact (保留)
    D. 不同 component + 同 data → 2 个 artifact (保留)
    E. 去重后 id 是 1..N 紧凑序列
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


def _seed_artifact_pair(
    session_factory: sessionmaker,
    *,
    sid: str,
    sequence: int,
    tool_call_id: str,
    artifact_args: dict[str, Any],
    success: bool = True,
) -> None:
    """Persist an assistant `present_artifact` + matching success tool result."""
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
        db.add(ChatMessage(
            session_id=sid,
            sequence=sequence + 1,
            role="tool",
            parts=[{
                "type": "raw",
                "schema": "openai-chat-completion",
                "data": {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": "present_artifact",
                    "content": "Artifact dispatched" if success else "Error: nope",
                },
            }],
        ))
        db.commit()
    finally:
        db.close()


def _login(
    client: TestClient, session_factory: sessionmaker, *, email: str
) -> tuple[int, dict[str, str]]:
    db = session_factory()
    try:
        u = seed_user(db, email=email, role="employee")
        uid = u.id
    finally:
        db.close()
    return uid, login(client, email)


def _artifacts(client: TestClient, sid: str, auth: dict[str, str]) -> list[dict[str, Any]]:
    res = client.get(f"/api/chat/sessions/{sid}", headers=auth)
    assert res.status_code == 200, res.text
    return res.json()["artifacts"]


def _table_args(narration: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "component": "generic_table",
        "narration": narration,
        "data": json.dumps({
            "title": "T",
            "columns": [{"key": "x", "label": "X"}],
            "rows": rows,
        }, ensure_ascii=False),
    }


# ─── A. exact duplicate ─────────────────────────────────────


def test_exact_duplicate_dispatch_collapses_to_one(
    client: TestClient, session_factory: sessionmaker
) -> None:
    uid, auth = _login(client, session_factory, email="dedup-a@v")
    sid = f"sess-dedup-a-{uid}"
    _seed_session(session_factory, user_id=uid, sid=sid)
    args = _table_args("Tokyo weather", [{"x": 1}])
    _seed_artifact_pair(session_factory, sid=sid, sequence=1, tool_call_id="t1", artifact_args=args)
    _seed_artifact_pair(session_factory, sid=sid, sequence=3, tool_call_id="t2", artifact_args=args)

    arts = _artifacts(client, sid, auth)
    assert len(arts) == 1
    assert arts[0]["id"] == 1


# ─── B. same data, different narration ─────────────────────


def test_same_data_different_narration_collapses_to_one(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Narration is excluded from the dedup key — only (component, data)
    matters. The LATER narration wins (newest dispatch overrides)."""
    uid, auth = _login(client, session_factory, email="dedup-b@v")
    sid = f"sess-dedup-b-{uid}"
    _seed_session(session_factory, user_id=uid, sid=sid)
    a1 = _table_args("Old narration", [{"x": 1}])
    a2 = _table_args("New narration", [{"x": 1}])
    _seed_artifact_pair(session_factory, sid=sid, sequence=1, tool_call_id="t1", artifact_args=a1)
    _seed_artifact_pair(session_factory, sid=sid, sequence=3, tool_call_id="t2", artifact_args=a2)

    arts = _artifacts(client, sid, auth)
    assert len(arts) == 1
    assert arts[0]["narration"] == "New narration"


# ─── C. different data → two artifacts ─────────────────────


def test_different_data_keeps_both_artifacts(
    client: TestClient, session_factory: sessionmaker
) -> None:
    uid, auth = _login(client, session_factory, email="dedup-c@v")
    sid = f"sess-dedup-c-{uid}"
    _seed_session(session_factory, user_id=uid, sid=sid)
    a1 = _table_args("Tokyo", [{"x": 1}])
    a2 = _table_args("Osaka", [{"x": 2}])
    _seed_artifact_pair(session_factory, sid=sid, sequence=1, tool_call_id="t1", artifact_args=a1)
    _seed_artifact_pair(session_factory, sid=sid, sequence=3, tool_call_id="t2", artifact_args=a2)

    arts = _artifacts(client, sid, auth)
    assert len(arts) == 2


# ─── D. different component → two artifacts ───────────────


def test_different_component_keeps_both_artifacts(
    client: TestClient, session_factory: sessionmaker
) -> None:
    uid, auth = _login(client, session_factory, email="dedup-d@v")
    sid = f"sess-dedup-d-{uid}"
    _seed_session(session_factory, user_id=uid, sid=sid)
    table_args = _table_args("Table", [{"x": 1}])
    diff_args = {
        "component": "upload_diff_viewer",
        "narration": "Diff",
        "data": json.dumps({"batch_id": 99}),
    }
    _seed_artifact_pair(session_factory, sid=sid, sequence=1, tool_call_id="t1", artifact_args=table_args)
    _seed_artifact_pair(session_factory, sid=sid, sequence=3, tool_call_id="t2", artifact_args=diff_args)

    arts = _artifacts(client, sid, auth)
    assert len(arts) == 2
    components = {a["component"] for a in arts}
    assert components == {"generic_table", "upload_diff_viewer"}


# ─── E. ids are compact 1..N after dedup ───────────────────


def test_ids_are_compact_after_dedup(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """The frontend's position counter reads `id` to render "X / N". After
    dedup we MUST reassign ids 1..N so gaps don't show up."""
    uid, auth = _login(client, session_factory, email="dedup-e@v")
    sid = f"sess-dedup-e-{uid}"
    _seed_session(session_factory, user_id=uid, sid=sid)
    a1 = _table_args("A", [{"x": 1}])
    a2 = _table_args("B", [{"x": 2}])
    a3 = _table_args("C", [{"x": 3}])
    # 5 dispatches: a1, a2, a1 (dup), a3, a2 (dup) → expect 3 unique.
    _seed_artifact_pair(session_factory, sid=sid, sequence=1, tool_call_id="t1", artifact_args=a1)
    _seed_artifact_pair(session_factory, sid=sid, sequence=3, tool_call_id="t2", artifact_args=a2)
    _seed_artifact_pair(session_factory, sid=sid, sequence=5, tool_call_id="t3", artifact_args=a1)
    _seed_artifact_pair(session_factory, sid=sid, sequence=7, tool_call_id="t4", artifact_args=a3)
    _seed_artifact_pair(session_factory, sid=sid, sequence=9, tool_call_id="t5", artifact_args=a2)

    arts = _artifacts(client, sid, auth)
    assert len(arts) == 3
    assert [a["id"] for a in arts] == [1, 2, 3]
