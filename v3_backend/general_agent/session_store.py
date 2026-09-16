"""Minimal SQLite session + message store — resume a conversation across runs.

Distilled from hermes_state.py (that file has ~1600 LOC with FTS5, billing,
parent_session_id lineage, schema migrations).  We keep:

  - sessions(id, model, system_prompt, title, started_at, ended_at)
  - messages(session_id, seq, role, content, tool_calls, tool_call_id, name, created_at)

Skipped (library scope):
  - FTS5 full-text search  (use grep over the DB directly if needed)
  - Billing / cost columns
  - parent_session_id compression lineage (our single-session compress doesn't split)
  - Schema versioning/migrations (one shot — rebuild if schema changes)
  - Concurrent-writer safety (one Agent per DB at a time)

Usage:

    store = SessionStore("./workspace/sessions.db")
    session_id = store.create(model="deepseek-chat", system_prompt="...")
    store.append(session_id, {"role": "user", "content": "hi"})
    # ... later / new process ...
    msgs = store.load(session_id)  # resume
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    model         TEXT,
    system_prompt TEXT,
    title         TEXT,
    started_at    REAL NOT NULL,
    ended_at      REAL,
    metadata      TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    session_id    TEXT NOT NULL,
    seq           INTEGER NOT NULL,
    role          TEXT NOT NULL,
    content       TEXT,
    tool_calls    TEXT,
    tool_call_id  TEXT,
    name          TEXT,
    created_at    REAL NOT NULL,
    PRIMARY KEY (session_id, seq),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id, seq);
"""


def _now() -> float:
    return time.time()


class SessionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---------- sessions ----------

    def create(
        self,
        *,
        model: str = "",
        system_prompt: str = "",
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        sid = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO sessions (id, model, system_prompt, title, started_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                sid,
                model,
                system_prompt,
                title or "",
                _now(),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        self._conn.commit()
        return sid

    def end_session(self, session_id: str) -> None:
        self._conn.execute(
            "UPDATE sessions SET ended_at = ? WHERE id = ?",
            (_now(), session_id),
        )
        self._conn.commit()

    def get(self, session_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, model, system_prompt, title, started_at, ended_at, metadata "
            "FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "model": row[1],
            "system_prompt": row[2],
            "title": row[3],
            "started_at": row[4],
            "ended_at": row[5],
            "metadata": json.loads(row[6] or "{}"),
        }

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, model, title, started_at, ended_at "
            "FROM sessions ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            n = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?", (r[0],)
            ).fetchone()[0]
            out.append({
                "id": r[0],
                "model": r[1],
                "title": r[2],
                "started_at": r[3],
                "ended_at": r[4],
                "messages": n,
            })
        return out

    def resolve_prefix(self, prefix: str) -> str | None:
        """Resolve a short prefix (first 8 chars) to a full session id."""
        rows = self._conn.execute(
            "SELECT id FROM sessions WHERE id LIKE ? LIMIT 2",
            (prefix + "%",),
        ).fetchall()
        if len(rows) == 1:
            return rows[0][0]
        return None

    # ---------- messages ----------

    def append(self, session_id: str, msg: dict[str, Any]) -> int:
        seq = self._next_seq(session_id)
        role = msg.get("role") or ""
        content = msg.get("content")
        if content is not None and not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        tool_calls_json = None
        if msg.get("tool_calls"):
            tool_calls_json = json.dumps(msg["tool_calls"], ensure_ascii=False, default=str)
        self._conn.execute(
            "INSERT INTO messages "
            "(session_id, seq, role, content, tool_calls, tool_call_id, name, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                seq,
                role,
                content,
                tool_calls_json,
                msg.get("tool_call_id"),
                msg.get("name"),
                _now(),
            ),
        )
        self._conn.commit()
        return seq

    def replace_last(self, session_id: str, msg: dict[str, Any]) -> None:
        """Rewrite the most recent message in place.

        Used when a post-streaming transform (e.g. the malformed-parallel-
        tool-call split) rewrites the in-memory message after it was
        persisted — without re-syncing the DB the persisted row would
        carry the pre-transform shape and the UI's call_id ↔ tool_result
        matching would break on session reload.
        """
        row = self._conn.execute(
            "SELECT seq FROM messages WHERE session_id = ? "
            "ORDER BY seq DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return
        seq = row[0]
        role = msg.get("role") or ""
        content = msg.get("content")
        if content is not None and not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        tool_calls_json = None
        if msg.get("tool_calls"):
            tool_calls_json = json.dumps(msg["tool_calls"], ensure_ascii=False, default=str)
        self._conn.execute(
            "UPDATE messages SET role=?, content=?, tool_calls=?, "
            "tool_call_id=?, name=? WHERE session_id=? AND seq=?",
            (
                role, content, tool_calls_json,
                msg.get("tool_call_id"), msg.get("name"),
                session_id, seq,
            ),
        )
        self._conn.commit()

    def replace_all(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """Wipe + rewrite all messages for a session.

        Used after an in-memory compaction — the summary message replaces the
        original middle turns, so we persist the compacted shape.
        """
        self._conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        for i, m in enumerate(messages):
            role = m.get("role") or ""
            content = m.get("content")
            if content is not None and not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            tool_calls_json = None
            if m.get("tool_calls"):
                tool_calls_json = json.dumps(m["tool_calls"], ensure_ascii=False, default=str)
            self._conn.execute(
                "INSERT INTO messages "
                "(session_id, seq, role, content, tool_calls, tool_call_id, name, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id, i, role, content, tool_calls_json,
                    m.get("tool_call_id"), m.get("name"), _now(),
                ),
            )
        self._conn.commit()

    def load(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT role, content, tool_calls, tool_call_id, name "
            "FROM messages WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
        out = []
        for role, content, tool_calls, tool_call_id, name in rows:
            msg: dict[str, Any] = {"role": role}
            if content is not None:
                msg["content"] = content
            if tool_calls:
                try:
                    msg["tool_calls"] = json.loads(tool_calls)
                except Exception:
                    pass
            if tool_call_id:
                msg["tool_call_id"] = tool_call_id
            if name:
                msg["name"] = name
            out.append(msg)
        return out

    def _next_seq(self, session_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row[0])

    # ---------- cleanup ----------

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
