"""chat + memory tables — Phase 6 agent platform groundwork.

Revision ID: 0005_chat_and_memory
Revises: 0004_inquiry_relational
Create Date: 2026-04-27

Creates v3-prefixed tables that coexist with v2's `v2_agent_*` during the
cutover window:
- v3_chat_sessions: chat-session metadata + token totals.
- v3_chat_messages: per-message parts JSON (engine canonical history).
- v3_agent_memories: cross-session long-term memory (Phase 6 W2 consumer).

Rollback: drop the three tables. No data is shared with v2 — v2 keeps
its own `v2_agent_*` rows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_chat_and_memory"
down_revision: str | Sequence[str] | None = "0004_inquiry_relational"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v3_chat_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("title", sa.String(500), server_default="新对话"),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("summary_message_id", sa.Integer, nullable=True),
        sa.Column("total_prompt_tokens", sa.Integer, server_default="0"),
        sa.Column("total_completion_tokens", sa.Integer, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Float, server_default="0"),
        sa.Column("end_reason", sa.String(30), nullable=True),
        sa.Column("parent_session_id", sa.String(36), nullable=True),
        sa.Column("context_data", sa.JSON, nullable=True),
        sa.Column("platform_type", sa.String(20), server_default="web"),
        sa.Column("platform_user_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_v3_chat_sessions_user_id", "v3_chat_sessions", ["user_id"])

    op.create_table(
        "v3_chat_messages",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("v3_chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("role", sa.String(15), nullable=False),
        sa.Column("parts", sa.JSON, nullable=False),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_v3_chat_messages_session_id", "v3_chat_messages", ["session_id"])

    op.create_table(
        "v3_agent_memories",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("memory_type", sa.String(30), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("source_session_id", sa.String(36), nullable=True),
        sa.Column("access_count", sa.Integer, server_default="0"),
        sa.Column("last_accessed_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
        sa.UniqueConstraint("user_id", "memory_type", "key", name="uq_v3_agent_memories_user_key"),
    )
    op.create_index("ix_v3_agent_memories_user_id", "v3_agent_memories", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_v3_agent_memories_user_id", table_name="v3_agent_memories")
    op.drop_table("v3_agent_memories")
    op.drop_index("ix_v3_chat_messages_session_id", table_name="v3_chat_messages")
    op.drop_table("v3_chat_messages")
    op.drop_index("ix_v3_chat_sessions_user_id", table_name="v3_chat_sessions")
    op.drop_table("v3_chat_sessions")
