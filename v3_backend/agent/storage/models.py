"""ORM tables for chat storage.

Tables are prefixed `v3_chat_*` to coexist with v2's `v2_agent_sessions` /
`v2_agent_messages` during the cutover window (Phase 7).

Schema notes:
- `ChatMessage.parts` is the canonical engine history (one JSON list per row).
  No dual-write to display messages — the frontend renders parts directly.
- `summary_message_id` points to a checkpoint message; older messages are
  hidden from history reconstruction (compaction support, Phase 6 W2).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.db.base import Base


class ChatSession(Base):
    __tablename__ = "v3_chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), default="新对话")
    status: Mapped[str] = mapped_column(String(20), default="active")
    summary_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Per-session chat model preference. NULL → fall back to settings
    # default (`AGENT_CHAT_MODEL`, e.g. `gemini-2.5-flash`). Provider is
    # auto-routed from the model name by `agent.runtime.llm._resolve_provider`.
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Token + cost accounting
    total_prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    # End-of-session metadata
    end_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    parent_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Loose JSON for referenced order ids etc.
    context_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Platform identification (web/line/wechat/...)
    platform_type: Mapped[str] = mapped_column(String(20), default="web")
    platform_user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ChatMessage(Base):
    __tablename__ = "v3_chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("v3_chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(15), nullable=False)  # user|assistant|tool
    parts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)


class PendingAction(Base):
    """HITL approval queue — `propose_action` writes here; the user
    confirms/rejects via `POST /api/chat/actions/{id}/decide`.

    The agent never executes Tier-2 (destructive / financial / batch /
    cross-user) operations directly. It writes a row here, the frontend
    renders an approval card, the user clicks, and only then does the
    HTTP layer dispatch to the corresponding domain service.

    `payload` carries the full argument blob (target id + fields). `result`
    is filled in when status flips to approved / rejected / failed; for
    "approved" it carries whatever the dispatched service returned (e.g.
    the deleted order id, the created supplier id), so the chat UI can
    cite the artifact.
    """

    __tablename__ = "v3_pending_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("v3_chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AgentMemory(Base):
    """Cross-session memory — written by `MemoryStore` / `V3Memory` adapter.

    Unique on (user_id, memory_type, key) so re-extracting the same fact
    updates the value rather than duplicating.
    """

    __tablename__ = "v3_agent_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    memory_type: Mapped[str] = mapped_column(String(30), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    source_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
