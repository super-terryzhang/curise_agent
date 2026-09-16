"""LINE domain ORM models.

Three tables, all `v3_line_*` prefix to coexist with v2's `line_users`:

- `v3_line_users`        — maps LINE userId ↔ internal User row
- `v3_line_bind_tokens`  — one-shot tokens for the LINE-Login-style bind flow
- `v3_line_event_log`    — webhook event-id dedup (LINE retries on timeout)

Channel scoping: `line_channel_id` is part of every key because LINE userIds
are channel-scoped — the same user gets a different userId in a different
bot channel. We store a short identifier (not the channel secret itself)
so a single deployment can in the future support a prod + test channel
without ID collisions.

DB writes go through `service.py`; do not touch these models directly
from `apps/*` — the architecture check (RULE-3) forbids it anyway.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.db.base import Base


class LineUser(Base):
    """One row per (LINE userId, channel) → internal User binding."""

    __tablename__ = "v3_line_users"
    __table_args__ = (
        UniqueConstraint(
            "line_user_id",
            "line_channel_id",
            name="uq_v3_line_users_line_id_channel",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # LINE-side identity. `line_user_id` is opaque per channel; never reuse
    # across channels. We store the channel ID alongside so a future
    # multi-channel deployment doesn't collide.
    line_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    line_channel_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="default"
    )

    # Internal user this LINE account is bound to. CASCADE so deactivating
    # the User wipes the binding too — prevents a stale orphan that would
    # otherwise grant access via the dead user_id.
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow
    )


class LineBindToken(Base):
    """One-shot bind token. Plaintext goes in the URL we send via LINE; only
    the SHA-256 hash is stored. After consumption the row stays for audit.
    """

    __tablename__ = "v3_line_bind_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # SHA-256 hex of the plaintext token. Plaintext is never stored.
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )

    # Who this token authorizes. Both fields needed to bind to the right
    # (line_user_id, channel) pair — we don't trust any input except this row.
    line_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    line_channel_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="default"
    )

    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow
    )


class LineEventLog(Base):
    """Webhook event-id deduplication.

    LINE retries on timeout / 5xx, so the same `event.id` can arrive twice.
    We INSERT the id; if it already exists, the SQL UNIQUE violation tells
    us to skip processing.
    """

    __tablename__ = "v3_line_event_log"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
