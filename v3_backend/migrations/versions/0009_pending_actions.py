"""HITL approval queue — `v3_pending_actions`.

Revision ID: 0009_pending_actions
Revises: 0008_line_users
Create Date: 2026-05-11

Backfill of a table that's existed in `agent.storage.models.PendingAction`
since Phase 6 W0 but was never given an Alembic migration. Tests using
`Base.metadata.create_all()` create it; production using Alembic-only
never had it. The bug surfaced when the LINE harness's G1 scenario tried
the `propose_action` tool against a user request to delete an order —
the INSERT failed with `no such table: v3_pending_actions`, and the
agent fell back to the generic "处理您的消息时出错了" reply.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_pending_actions"
down_revision: str | Sequence[str] | None = "0008_line_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v3_pending_actions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("v3_chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("target_kind", sa.String(30), nullable=False),
        sa.Column("target_id", sa.Integer, nullable=True),
        sa.Column("payload", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("result", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("decided_at", sa.DateTime, nullable=True),
        sa.Column("decided_by", sa.Integer, nullable=True),
    )
    op.create_index(
        "ix_v3_pending_actions_session_id",
        "v3_pending_actions",
        ["session_id"],
    )
    op.create_index(
        "ix_v3_pending_actions_user_id",
        "v3_pending_actions",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_v3_pending_actions_user_id", table_name="v3_pending_actions"
    )
    op.drop_index(
        "ix_v3_pending_actions_session_id", table_name="v3_pending_actions"
    )
    op.drop_table("v3_pending_actions")
