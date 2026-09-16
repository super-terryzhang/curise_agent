"""Revocable sessions, bounded temporary credentials, security events and rate windows."""

import sqlalchemy as sa
from alembic import op

revision = "0024_auth_hardening"
down_revision = "0023_document_folder_color"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("temporary_password_expires_at", sa.DateTime(), nullable=True))
    # Existing temporary accounts are expired, never silently granted full access.
    # Model timestamps are naive UTC; a non-UTC PostgreSQL server must not
    # accidentally extend old temporary credentials by its timezone offset.
    now_sql = (
        "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"
        if op.get_bind().dialect.name == "postgresql"
        else "CURRENT_TIMESTAMP"
    )
    op.execute(
        f"UPDATE users SET temporary_password_expires_at = {now_sql} WHERE is_default_password = true"
    )
    op.create_table(
        "v3_auth_sessions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("is_revoked", sa.Boolean(), nullable=False),
        sa.Column("restricted", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_v3_auth_sessions_user_id", "v3_auth_sessions", ["user_id"])
    op.add_column("v2_refresh_tokens", sa.Column("session_id", sa.String(32), nullable=True))
    op.add_column("v2_refresh_tokens", sa.Column("rotated_at", sa.DateTime(), nullable=True))
    op.create_index("ix_v2_refresh_tokens_session_id", "v2_refresh_tokens", ["session_id"])
    with op.batch_alter_table("v2_refresh_tokens") as batch:
        batch.create_foreign_key(
            "fk_refresh_auth_session", "v3_auth_sessions", ["session_id"], ["id"]
        )
    # Old JWTs have no session ID and will require login after application cutover.
    op.execute("UPDATE v2_refresh_tokens SET is_revoked = true WHERE session_id IS NULL")
    op.create_table(
        "v3_security_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.Integer()),
        sa.Column("target_id", sa.Integer()),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("request_id", sa.String(32)),
        sa.Column("source_hash", sa.String(64)),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
    )
    op.create_index("ix_v3_security_events_occurred_at", "v3_security_events", ["occurred_at"])
    op.create_index("ix_v3_security_events_event_type", "v3_security_events", ["event_type"])
    op.create_table(
        "v3_auth_rate_windows",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("window", sa.Integer(), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
    )


def downgrade():
    raise RuntimeError(
        "Security rollback must retain revocations and audit history; use a compatible application version."
    )
