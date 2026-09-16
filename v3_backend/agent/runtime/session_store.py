"""V3SessionStore — SQLAlchemy-backed session store for general-agent.

Duck-types `general_agent.SessionStore` (the SQLite default) so it can be
passed as `AgentConfig.session_store=...`. Persists into v3's ORM tables:

- `v3_chat_sessions` (one row per chat session, scoped to a `user_id`)
- `v3_chat_messages` (one row per agent message, with `parts` JSON)

Cross-user isolation is enforced at construction time: every instance
binds to a single `user_id`, and every read/write filters on it. A
malformed call asking for another user's session returns None / [] /
raises — never a leak.

The `parts` column stores general-agent's raw message dict (`{role,
content, tool_calls, tool_call_id, name}`) verbatim. We don't decompose
into a per-field schema — keeping the OpenAI Chat Completions shape
intact means tool_call payloads round-trip losslessly without us
having to mirror every minor LLM-side schema change.

ADR-0006 RULE-2 status: this module is in `agent/runtime/`, which is on
the white-list for direct DB access (same as `agent/storage/`). Business
tools must NOT instantiate this directly — only the chat HTTP factory
constructs it.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as SqlaSession

from agent.storage.models import ChatMessage, ChatSession

logger = logging.getLogger(__name__)


class V3SessionStore:
    """User-scoped session store backed by `v3_chat_*` tables.

    Construction binds a `db` session and `user_id`. The store is intended
    to live for a single agent run; the host application creates a fresh
    instance per HTTP request (FastAPI dependency-injection style).
    """

    def __init__(self, db: SqlaSession, *, user_id: int):
        self._db = db
        self._user_id = user_id

    # ─── Session CRUD (general-agent SessionStore surface) ───

    def create(
        self,
        *,
        model: str = "",
        system_prompt: str = "",
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Insert a new chat session, return its id (UUID-based, hex 32 chars).

        `model` / `system_prompt` are recorded for debugging only — they don't
        change agent behavior. `metadata` is stored under `context_data` JSON.
        """
        sid = uuid.uuid4().hex
        row = ChatSession(
            id=sid,
            user_id=self._user_id,
            title=title or "新对话",
            context_data={
                "model": model,
                "system_prompt_preview": (system_prompt or "")[:200],
                "extras": metadata or {},
            },
        )
        self._db.add(row)
        self._db.commit()
        return sid

    def get(self, session_id: str) -> dict[str, Any] | None:
        row = self._db.get(ChatSession, session_id)
        if row is None or row.user_id != self._user_id:
            return None
        ctx = row.context_data or {}
        return {
            "id": row.id,
            "model": ctx.get("model", ""),
            "system_prompt": ctx.get("system_prompt_preview", ""),
            "title": row.title,
            "started_at": row.created_at.timestamp() if row.created_at else 0.0,
            "ended_at": None,  # v3 schema doesn't track ended_at separately
            "metadata": ctx.get("extras", {}),
        }

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        stmt = (
            select(ChatSession)
            .where(ChatSession.user_id == self._user_id)
            .order_by(ChatSession.updated_at.desc())
            .limit(limit)
        )
        rows = list(self._db.execute(stmt).scalars())
        out: list[dict[str, Any]] = []
        for r in rows:
            n = self._db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == r.id)
            ).all()
            out.append(
                {
                    "id": r.id,
                    "model": (r.context_data or {}).get("model", ""),
                    "title": r.title,
                    "started_at": r.created_at.timestamp() if r.created_at else 0.0,
                    "ended_at": None,
                    "messages": len(n),
                }
            )
        return out

    def resolve_prefix(self, prefix: str) -> str | None:
        """Resolve a short id prefix (first 8 chars) to a full session id."""
        if not prefix:
            return None
        stmt = (
            select(ChatSession.id)
            .where(
                ChatSession.user_id == self._user_id,
                ChatSession.id.like(f"{prefix}%"),
            )
            .limit(2)
        )
        rows = list(self._db.execute(stmt))
        if len(rows) == 1:
            return rows[0][0]
        return None

    def end_session(self, session_id: str) -> None:
        # v3 schema doesn't track ended_at distinctly; we no-op here.
        # If future audit needs an end timestamp, add `end_reason` write here.
        del session_id

    # ─── Messages ────────────────────────────────────────────

    def append(self, session_id: str, msg: dict[str, Any]) -> int:
        """Append one message. `msg` is general-agent's raw dict shape.

        Returns the sequence number assigned (0-indexed within the session).
        """
        if not self._owns(session_id):
            raise PermissionError(
                f"session {session_id} not owned by user {self._user_id}"
            )
        seq = self._next_seq(session_id)
        row = ChatMessage(
            session_id=session_id,
            sequence=seq,
            role=msg.get("role") or "",
            parts=_msg_to_parts(msg),
            model=(msg.get("model") if isinstance(msg.get("model"), str) else None),
        )
        self._db.add(row)
        # Bump session.updated_at so chat list orders by recency.
        sess = self._db.get(ChatSession, session_id)
        if sess is not None:
            from datetime import datetime as _dt

            sess.updated_at = _dt.utcnow()
        self._db.commit()
        return seq

    def replace_last(self, session_id: str, msg: dict[str, Any]) -> None:
        """Rewrite the most recent message's parts in place.

        Used when a post-streaming transform (e.g. malformed-parallel-
        tool-call split in `general_agent/core.py`) rewrites the
        in-memory assistant message after it's already been persisted.
        Without re-syncing the DB the persisted row keeps the pre-split
        concatenated `tool_call_id` while the tool result rows use the
        synthetic split IDs — frontend's `stepById.get(callId)` then
        misses and the UI shows an orphan "running" card forever
        (prod 2026-05-19 repro).
        """
        if not self._owns(session_id):
            raise PermissionError(
                f"session {session_id} not owned by user {self._user_id}"
            )
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.sequence.desc())
            .limit(1)
        )
        row = self._db.execute(stmt).scalar_one_or_none()
        if row is None:
            return
        row.role = msg.get("role") or row.role
        row.parts = _msg_to_parts(msg)
        self._db.commit()

    def replace_all(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """Wipe and rewrite messages for a session.

        Called after in-memory compaction — the LLM-generated summary
        replaces middle turns, and we persist the compact shape so resume
        sees the same compressed history.
        """
        if not self._owns(session_id):
            raise PermissionError(
                f"session {session_id} not owned by user {self._user_id}"
            )
        self._db.execute(
            ChatMessage.__table__.delete().where(ChatMessage.session_id == session_id)
        )
        for i, m in enumerate(messages):
            self._db.add(
                ChatMessage(
                    session_id=session_id,
                    sequence=i,
                    role=m.get("role") or "",
                    parts=_msg_to_parts(m),
                    model=(m.get("model") if isinstance(m.get("model"), str) else None),
                )
            )
        self._db.commit()

    def load(self, session_id: str) -> list[dict[str, Any]]:
        if not self._owns(session_id):
            return []
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.sequence.asc())
        )
        rows = list(self._db.execute(stmt).scalars())
        return [_parts_to_msg(r.parts or []) for r in rows]

    # ─── Internal ────────────────────────────────────────────

    def _owns(self, session_id: str) -> bool:
        row = self._db.get(ChatSession, session_id)
        return row is not None and row.user_id == self._user_id

    def _next_seq(self, session_id: str) -> int:
        stmt = (
            select(ChatMessage.sequence)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.sequence.desc())
            .limit(1)
        )
        row = self._db.execute(stmt).first()
        return (row[0] + 1) if row else 0


# ─── Message ↔ parts JSON encoding ───────────────────────────


def _msg_to_parts(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Encode a general-agent message dict into a single 'raw' part.

    Lossless: every field general-agent emits (role / content / tool_calls /
    tool_call_id / name) is kept inside `data`. The `type=raw` envelope tells
    future readers that the payload follows OpenAI Chat Completions schema,
    not v3's old per-part schema.

    Role is duplicated into `ChatMessage.role` (column) AND `data.role` (parts
    JSON). The column lets us SQL-filter by role; the JSON makes round-trip
    lossless without depending on the column.
    """
    return [
        {
            "type": "raw",
            "schema": "openai-chat-completion",
            "data": dict(msg),  # full snapshot, role included
        }
    ]


def _parts_to_msg(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Reverse of `_msg_to_parts`. Robust to malformed rows (defensive)."""
    if not parts:
        return {"role": "user", "content": ""}
    first = parts[0] if isinstance(parts[0], dict) else {}
    payload = first.get("data") or {}
    if not isinstance(payload, dict):
        payload = {}
    msg: dict[str, Any] = dict(payload)
    msg.setdefault("role", "user")
    return msg
