"""LINE integration tables — bind tokens, user mapping, event dedup.

Revision ID: 0008_line_users
Revises: 0007_user_tags
Create Date: 2026-05-10

Three new tables, all `v3_line_*` prefix to coexist with v2's `line_users`:

- v3_line_users       — maps (LINE userId, channel) → internal User
- v3_line_bind_tokens — one-shot bind tokens (SHA-256 hash at rest)
- v3_line_event_log   — webhook event-id dedup

Rollback: drop the three tables. No data is shared with v2.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_line_users"
down_revision: str | Sequence[str] | None = "0007_user_tags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v3_line_users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("line_user_id", sa.String(64), nullable=False),
        sa.Column(
            "line_channel_id",
            sa.String(64),
            nullable=False,
            server_default="default",
        ),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column(
            "is_blocked",
            sa.Boolean,
            nullable=False,
            # Use `false` (not `'0'`) — Postgres rejects int→bool implicit cast.
            server_default=sa.text("false"),
        ),
        sa.Column("last_active_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.UniqueConstraint(
            "line_user_id",
            "line_channel_id",
            name="uq_v3_line_users_line_id_channel",
        ),
    )
    op.create_index(
        "ix_v3_line_users_line_user_id", "v3_line_users", ["line_user_id"]
    )
    op.create_index("ix_v3_line_users_user_id", "v3_line_users", ["user_id"])

    op.create_table(
        "v3_line_bind_tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("line_user_id", sa.String(64), nullable=False),
        sa.Column(
            "line_channel_id",
            sa.String(64),
            nullable=False,
            server_default="default",
        ),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("consumed_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )
    op.create_index(
        "ix_v3_line_bind_tokens_token_hash",
        "v3_line_bind_tokens",
        ["token_hash"],
    )

    op.create_table(
        "v3_line_event_log",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("received_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("v3_line_event_log")
    op.drop_index(
        "ix_v3_line_bind_tokens_token_hash", table_name="v3_line_bind_tokens"
    )
    op.drop_table("v3_line_bind_tokens")
    op.drop_index("ix_v3_line_users_user_id", table_name="v3_line_users")
    op.drop_index("ix_v3_line_users_line_user_id", table_name="v3_line_users")
    op.drop_table("v3_line_users")
