"""Data access for identity domain.

All SQL queries involving User / RefreshToken go through this module.
Service layer never calls `db.query(User)` directly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from domains.identity.models import RefreshToken, User

# ─── User queries ─────────────────────────────────────────────


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email).first()


def list_active_users(db: Session) -> list[User]:
    return db.query(User).filter(User.is_active.is_(True)).order_by(User.id).all()


def list_all_users(db: Session) -> list[User]:
    return db.query(User).order_by(User.id).all()


# ─── User mutations ───────────────────────────────────────────


def create_user_row(
    db: Session,
    *,
    email: str,
    hashed_password: str,
    full_name: str,
    role: str,
) -> User:
    user = User(
        email=email,
        hashed_password=hashed_password,
        full_name=full_name,
        role=role,
        is_active=True,
        is_default_password=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def save_user(db: Session, user: User) -> User:
    db.commit()
    db.refresh(user)
    return user


def update_login_success(db: Session, user: User) -> None:
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login = datetime.now(UTC)
    db.commit()


def update_login_failure(db: Session, user: User, lock_threshold: int, lock_minutes: int) -> int:
    """Increment failure counter; lock account if threshold reached.

    Returns the remaining attempts before lock.
    """
    user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
    user.last_failed_login = datetime.now(UTC)
    if user.failed_login_attempts >= lock_threshold:
        user.locked_until = datetime.now(UTC) + timedelta(minutes=lock_minutes)
    db.commit()
    return max(0, lock_threshold - user.failed_login_attempts)


def update_password(db: Session, user: User, hashed: str) -> None:
    user.hashed_password = hashed
    user.is_default_password = False
    user.password_changed_at = datetime.now(UTC)
    db.commit()


# ─── Refresh token queries ────────────────────────────────────


def find_active_refresh_token(db: Session, token_hash: str) -> RefreshToken | None:
    return (
        db.query(RefreshToken)
        .filter(
            RefreshToken.token_hash == token_hash,
            RefreshToken.is_revoked.is_(False),
            RefreshToken.expires_at > datetime.now(UTC).replace(tzinfo=None),
        )
        .first()
    )


def insert_refresh_token(
    db: Session,
    *,
    user_id: int,
    token_hash: str,
    expires_at: datetime,
) -> RefreshToken:
    rt = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
    db.add(rt)
    db.commit()
    db.refresh(rt)
    return rt


def revoke_refresh_token_by_hash(db: Session, token_hash: str) -> None:
    rt = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if rt is not None:
        rt.is_revoked = True
        db.commit()


def revoke_all_user_tokens(db: Session, user_id: int) -> int:
    """Revoke every active refresh token owned by this user. Returns count."""
    updated = (
        db.query(RefreshToken)
        .filter(RefreshToken.user_id == user_id, RefreshToken.is_revoked.is_(False))
        .update({RefreshToken.is_revoked: True}, synchronize_session=False)
    )
    db.commit()
    return int(updated)
