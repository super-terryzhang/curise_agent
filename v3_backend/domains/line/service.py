"""LINE business logic — single entry point for bind / lookup / event dedup.

Errors are raised as `LineError` subclasses; the HTTP / webhook layers
translate them. Service functions never raise HTTPException directly so
they're equally usable from a CLI or a future job runner.

Security notes:
- `generate_bind_token` returns plaintext exactly once; the DB only ever
  stores the SHA-256 hash. Plaintext is delivered to the LINE user via
  the bot's reply (i.e. only that LINE userId can see it).
- `consume_bind_token` is single-use: a row with `consumed_at != NULL`
  is rejected even if it hasn't expired.
- Both `find_user_by_line_id` and `bind_user` enforce the (line_user_id,
  channel) composite — never trust just the `line_user_id` alone.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domains.line.models import LineBindToken, LineEventLog, LineUser

# ─── Errors ───────────────────────────────────────────────────


class LineError(Exception):
    """Base for LINE domain errors."""


class BindTokenInvalid(LineError):
    """Token doesn't exist (or someone tampered with it)."""


class BindTokenExpired(LineError):
    """Token existed but is past its expiry."""


class BindTokenAlreadyUsed(LineError):
    """Token was already consumed once. One-shot — don't accept twice."""


class LineUserAlreadyBound(LineError):
    """Trying to bind a (line_user_id, channel) that's already mapped to
    a different internal user. Re-binding to the same user is fine
    (idempotent)."""


# ─── Token hashing ────────────────────────────────────────────


def _hash_token(plaintext: str) -> str:
    """SHA-256 hex. Stored at rest so DB compromise doesn't leak tokens."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


# ─── User lookup / bind ───────────────────────────────────────


def find_user_by_line_id(
    db: Session, *, line_user_id: str, channel_id: str = "default"
) -> LineUser | None:
    """Return the LineUser row for this (LINE userId, channel) or None.

    Channel scoping is mandatory — passing only `line_user_id` would
    silently match across channels and leak the binding.
    """
    stmt = select(LineUser).where(
        LineUser.line_user_id == line_user_id,
        LineUser.line_channel_id == channel_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def bind_user(
    db: Session,
    *,
    line_user_id: str,
    channel_id: str = "default",
    internal_user_id: int,
    display_name: str | None = None,
) -> LineUser:
    """Idempotently bind a (LINE userId, channel) to an internal user.

    - Same target user → updates display_name + last_active_at, returns the row.
    - Different target user → raises `LineUserAlreadyBound` (don't silently
      hijack an existing binding).
    """
    existing = find_user_by_line_id(
        db, line_user_id=line_user_id, channel_id=channel_id
    )
    if existing is not None:
        if existing.user_id != internal_user_id:
            raise LineUserAlreadyBound(
                f"LINE userId {line_user_id[:8]}... already bound to a "
                f"different internal user"
            )
        if display_name is not None:
            existing.display_name = display_name
        existing.last_active_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing

    row = LineUser(
        line_user_id=line_user_id,
        line_channel_id=channel_id,
        user_id=internal_user_id,
        display_name=display_name,
        is_blocked=False,
        last_active_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_last_active(db: Session, *, line_user: LineUser) -> None:
    line_user.last_active_at = datetime.utcnow()
    db.commit()


# ─── Bind token lifecycle ─────────────────────────────────────


def generate_bind_token(
    db: Session,
    *,
    line_user_id: str,
    channel_id: str = "default",
    ttl_minutes: int = 30,
) -> str:
    """Mint a one-shot bind token. Returns plaintext — store nothing.

    Plaintext is 256 bits of urlsafe entropy — sufficient that the user
    can't guess one and infinite combinations brute-force. Hash is what
    we store + later compare against.
    """
    plaintext = secrets.token_urlsafe(32)  # 256 bits
    row = LineBindToken(
        token_hash=_hash_token(plaintext),
        line_user_id=line_user_id,
        line_channel_id=channel_id,
        expires_at=datetime.utcnow() + timedelta(minutes=ttl_minutes),
    )
    db.add(row)
    db.commit()
    return plaintext


def consume_bind_token(
    db: Session, *, plaintext: str
) -> tuple[str, str]:
    """Validate + mark consumed. Returns the (line_user_id, channel_id).

    Raises:
      BindTokenInvalid     — no row matches the hash
      BindTokenExpired     — expired_at in the past
      BindTokenAlreadyUsed — consumed_at already set
    """
    token_hash = _hash_token(plaintext)
    stmt = select(LineBindToken).where(LineBindToken.token_hash == token_hash)
    row = db.execute(stmt).scalar_one_or_none()
    if row is None:
        raise BindTokenInvalid("绑定令牌无效或已被撤销")

    if row.consumed_at is not None:
        raise BindTokenAlreadyUsed("绑定令牌已被使用，请重新发起绑定")

    now = datetime.utcnow()
    if row.expires_at < now:
        raise BindTokenExpired("绑定令牌已过期，请重新发起绑定")

    row.consumed_at = now
    db.commit()
    return row.line_user_id, row.line_channel_id


# ─── Webhook event dedup ──────────────────────────────────────


def record_event_id(db: Session, *, event_id: str) -> bool:
    """Record an inbound LINE webhook event-id. Returns True if new, False if dup.

    LINE retries on timeout / 5xx, so the same event.id arrives twice.
    We INSERT the id; if it conflicts (UNIQUE violation), it's a duplicate
    and the caller should skip processing.
    """
    if not event_id:
        # Defensive: LINE always sends an id, but if it doesn't there's
        # nothing to dedup against — treat as new and let downstream guards
        # (session-status row lock, agent idempotency) catch any double-fire.
        return True

    row = LineEventLog(event_id=event_id, received_at=datetime.utcnow())
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    return True
