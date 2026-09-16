"""Security events are persisted in the same transaction as identity changes."""

import hashlib
import hmac
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from domains.identity.models import AuthSession, RefreshToken, SecurityEvent
from infrastructure.config import settings

request_context: ContextVar[dict | None] = ContextVar("security_request", default=None)
_DETAIL_KEYS = {
    "reason",
    "role_before",
    "role_after",
    "active_before",
    "active_after",
    "capability",
}


def fingerprint(value: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def record(
    db: Session,
    event_type: str,
    *,
    actor_id: int | None = None,
    target_id: int | None = None,
    outcome: str = "success",
    **details,
) -> None:
    if set(details) - _DETAIL_KEYS:
        raise ValueError("unapproved security event fields")
    context = request_context.get() or {}
    db.add(
        SecurityEvent(
            id=uuid.uuid4().hex,
            event_type=event_type,
            actor_id=actor_id,
            target_id=target_id,
            outcome=outcome,
            request_id=context.get("request_id"),
            source_hash=context.get("source_hash"),
            channel=context.get("channel", "internal"),
            details=details,
        )
    )


def purge_expired(db: Session, *, apply: bool = False) -> int:
    """Retention maintenance; dry-run by default, never shorten configured retention."""
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        days=settings.SECURITY_EVENT_RETENTION_DAYS
    )
    predicate = SecurityEvent.occurred_at < cutoff
    count = db.scalar(select(func.count()).select_from(SecurityEvent).where(predicate)) or 0
    if apply:
        db.execute(delete(SecurityEvent).where(predicate))
        db.commit()
    return count


def purge_expired_sessions(db: Session, *, apply: bool = False) -> int:
    """Keep unexpired revoked sessions for replay detection; prune only absolute expiry."""
    now = datetime.now(UTC).replace(tzinfo=None)
    expired = select(AuthSession.id).where(AuthSession.expires_at < now)
    count = (
        db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.expires_at < now))
        or 0
    )
    if apply:
        db.execute(
            delete(RefreshToken).where(
                (RefreshToken.expires_at < now) | RefreshToken.session_id.in_(expired)
            )
        )
        db.execute(delete(AuthSession).where(AuthSession.expires_at < now))
        db.commit()
    return count
