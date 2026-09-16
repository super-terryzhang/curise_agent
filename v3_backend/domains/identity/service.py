"""Identity business logic.

Single entry point for all auth flows. HTTP layer, Agent tools, CLI scripts
and tests all go through these functions.

Errors are raised as `AuthError` subclasses; the HTTP layer translates them
to HTTP status codes. Service functions never raise HTTPException directly.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from domains.identity import audit
from domains.identity import repository as repo
from domains.identity.models import AuthSession, RefreshToken, User
from domains.identity.passwords import password_error
from domains.identity.schemas import TokenResponse, UserResponse
from infrastructure.config import settings
from infrastructure.security import (
    ROLE_LEVELS,
    InvalidToken,
    canonicalize_role,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)

# ─── Errors ───────────────────────────────────────────────────


class AuthError(Exception):
    """Base for authentication/authorization errors."""


class InvalidCredentials(AuthError):
    def __init__(self, remaining_attempts: int | None = None) -> None:
        self.remaining_attempts = remaining_attempts
        super().__init__("邮箱或密码错误")


class AccountLocked(AuthError):
    def __init__(self, minutes_remaining: int) -> None:
        self.minutes_remaining = minutes_remaining
        super().__init__(f"账号已锁定，请 {minutes_remaining} 分钟后重试")


class AccountInactive(AuthError):
    pass


class InvalidRefreshToken(AuthError):
    pass


class WrongCurrentPassword(AuthError):
    pass


class WeakPassword(AuthError):
    pass


class InvalidRole(AuthError):
    pass


class EmailAlreadyExists(AuthError):
    pass


class UserNotFound(AuthError):
    pass


class CannotDeactivateSelf(AuthError):
    pass


def get_business_user(db: Session, user_id: int) -> User:
    """Reload current identity at every channel/tool execution boundary."""
    user = db.get(User, user_id, populate_existing=True)
    if user is None or not user.is_active:
        raise AccountInactive("账号不可用")
    if user.is_default_password:
        raise AccountInactive("请先修改临时密码")
    return user


def has_capability(db: Session, user_id: int, capability: str) -> bool:
    try:
        user = get_business_user(db, user_id)
    except AccountInactive:
        return False
    return user.role == "superadmin" or capability in _load_capability_keys(db, user_id)


# ─── Core token helpers ───────────────────────────────────────


def _serialize_user(db: Session, user: User) -> UserResponse:
    """Build the UserResponse for HTTP — including the user's granted
    capability keys. Single source of truth so the login response,
    refresh response, and /auth/me all carry the same shape."""
    resp = UserResponse.model_validate(user)
    resp.capabilities = _load_capability_keys(db, user.id)
    return resp


def _load_capability_keys(db: Session, user_id: int) -> list[str]:
    from sqlalchemy import select

    from domains.identity.models import UserCapability

    rows = db.execute(
        select(UserCapability.capability).where(UserCapability.user_id == user_id)
    ).all()
    return [r[0] for r in rows]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _locked_user(db: Session, user_id: int) -> User:
    user = db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise UserNotFound("用户不存在")
    return user


def _revoke_sessions(db: Session, user_id: int, session_id: str | None = None) -> None:
    sessions = update(AuthSession).where(AuthSession.user_id == user_id)
    tokens = update(RefreshToken).where(RefreshToken.user_id == user_id)
    if session_id is not None:
        sessions = sessions.where(AuthSession.id == session_id)
        tokens = tokens.where(RefreshToken.session_id == session_id)
    db.execute(sessions.values(is_revoked=True))
    db.execute(tokens.values(is_revoked=True))


def _issue_tokens(
    db: Session, user: User, *, session: AuthSession | None = None, raw_refresh: str | None = None
) -> TokenResponse:
    now = _now()
    if session is None:
        restricted = user.is_default_password
        expiry = now + timedelta(days=settings.SESSION_MAX_DAYS)
        if restricted:
            if (
                user.temporary_password_expires_at is None
                or user.temporary_password_expires_at <= now
            ):
                raise InvalidCredentials()
            expiry = min(
                user.temporary_password_expires_at,
                now + timedelta(minutes=settings.RESTRICTED_SESSION_MINUTES),
            )
        session = AuthSession(
            id=uuid.uuid4().hex,
            user_id=user.id,
            expires_at=expiry,
            is_revoked=False,
            restricted=restricted,
        )
        db.add(session)
        db.flush()
    access = create_access_token(
        user.id,
        user.role,
        expires_delta=min(
            timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES), session.expires_at - now
        ),
        session_id=session.id,
        restricted=session.restricted,
    )
    raw = raw_refresh or generate_refresh_token()
    if (
        not session.restricted
        and db.scalar(
            select(RefreshToken.id).where(RefreshToken.token_hash == hash_refresh_token(raw))
        )
        is None
    ):
        db.add(
            RefreshToken(
                user_id=user.id,
                session_id=session.id,
                token_hash=hash_refresh_token(raw),
                expires_at=min(
                    session.expires_at, now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
                ),
                is_revoked=False,
            )
        )
    return TokenResponse(
        access_token=access,
        refresh_token="" if session.restricted else raw,
        user=_serialize_user(db, user),
    )


def login(db: Session, *, email: str, password: str) -> TokenResponse:
    user = repo.get_user_by_email(db, email)
    if user is None:
        # Same expensive verification path without creating an account or logging email.
        verify_password(password, _DUMMY_HASH)
        audit.record(db, "login", outcome="denied", reason="invalid_credentials")
        db.commit()
        raise InvalidCredentials()
    user = _locked_user(db, user.id)
    now = _now()
    valid = verify_password(password, user.hashed_password)
    if not user.is_active or (user.locked_until and user.locked_until > now):
        audit.record(db, "login", target_id=user.id, outcome="denied", reason="invalid_credentials")
        db.commit()
        raise InvalidCredentials()
    if user.locked_until and user.locked_until <= now:
        user.failed_login_attempts = 0
        user.locked_until = None
    if not valid:
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        user.last_failed_login = now
        if user.failed_login_attempts >= settings.MAX_FAILED_LOGIN_ATTEMPTS:
            user.locked_until = now + timedelta(minutes=settings.ACCOUNT_LOCK_MINUTES)
        audit.record(db, "login", target_id=user.id, outcome="denied", reason="invalid_credentials")
        db.commit()
        raise InvalidCredentials()
    if user.is_default_password and (
        user.temporary_password_expires_at is None or user.temporary_password_expires_at <= now
    ):
        audit.record(db, "login", target_id=user.id, outcome="denied", reason="temporary_expired")
        db.commit()
        raise InvalidCredentials()
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login = now
    result = _issue_tokens(db, user)
    audit.record(db, "login", actor_id=user.id, target_id=user.id)
    db.commit()
    return result


# Precomputed once, same hashing scheme as newly created credentials.
_DUMMY_HASH = hash_password("synthetic-password-for-unknown-account")


def refresh_tokens(db: Session, *, refresh_token: str) -> TokenResponse:
    token_hash = hash_refresh_token(refresh_token)
    rt = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if rt is None or rt.session_id is None:
        raise InvalidRefreshToken("无效的刷新凭证")
    user = _locked_user(db, rt.user_id)
    db.refresh(rt)
    session = db.get(AuthSession, rt.session_id, populate_existing=True)
    now = _now()
    if (
        not user.is_active
        or user.is_default_password
        or session is None
        or session.is_revoked
        or session.restricted
        or session.expires_at <= now
        or rt.expires_at <= now
    ):
        raise InvalidRefreshToken("会话已失效，请重新登录")
    successor = hmac.new(
        settings.SECRET_KEY.encode(), ("refresh-v1:" + refresh_token).encode(), hashlib.sha256
    ).hexdigest()
    if rt.is_revoked:
        child = db.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(successor))
        )
        in_window = (
            rt.rotated_at is not None
            and (now - rt.rotated_at).total_seconds() <= settings.REFRESH_RETRY_SECONDS
        )
        # A later tab may already have consumed the direct successor before
        # an earlier response is retried. Follow the same bounded lineage;
        # never mint a second branch or extend the original retry window.
        for _ in range(16):
            if not in_window or child is None or not child.is_revoked or child.rotated_at is None:
                break
            successor = hmac.new(
                settings.SECRET_KEY.encode(), ("refresh-v1:" + successor).encode(), hashlib.sha256
            ).hexdigest()
            child = db.scalar(
                select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(successor))
            )
        retry_ok = (
            in_window and child is not None and not child.is_revoked and child.expires_at > now
        )
        if not retry_ok:
            _revoke_sessions(db, user.id, session.id)
            audit.record(
                db,
                "refresh_replay",
                target_id=user.id,
                outcome="denied",
                reason="outside_retry_window",
            )
            db.commit()
            raise InvalidRefreshToken("会话已失效，请重新登录")
    else:
        consumed = db.execute(
            update(RefreshToken)
            .where(RefreshToken.id == rt.id, RefreshToken.is_revoked.is_(False))
            .values(is_revoked=True, rotated_at=now)
        ).rowcount
        if consumed != 1:
            db.rollback()
            raise InvalidRefreshToken("刷新冲突，请重试")
    result = _issue_tokens(db, user, session=session, raw_refresh=successor)
    db.commit()
    return result


def logout(db: Session, *, refresh_token: str) -> None:
    rt = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(refresh_token))
    )
    if rt is not None and rt.session_id is not None:
        _locked_user(db, rt.user_id)
        _revoke_sessions(db, rt.user_id, rt.session_id)
        audit.record(db, "logout", actor_id=rt.user_id, target_id=rt.user_id)
        db.commit()


def change_password(
    db: Session, *, user: User, current_password: str, new_password: str
) -> TokenResponse:
    user = _locked_user(db, user.id)
    if sid := db.info.get("auth_session_id"):
        assert_session_active(db, user.id, sid, allow_restricted=True)
    if not user.is_active or not verify_password(current_password, user.hashed_password):
        raise WrongCurrentPassword("当前密码错误或账号不可用")
    if user.is_default_password and (
        user.temporary_password_expires_at is None or user.temporary_password_expires_at <= _now()
    ):
        raise WrongCurrentPassword("临时凭证已过期，请联系管理员")
    error = password_error(new_password)
    if error:
        raise WeakPassword(error)
    user.hashed_password = hash_password(new_password)
    user.is_default_password = False
    user.temporary_password_expires_at = None
    user.password_changed_at = _now()
    user.failed_login_attempts = 0
    user.locked_until = None
    _revoke_sessions(db, user.id)
    result = _issue_tokens(db, user)
    audit.record(db, "password_changed", actor_id=user.id, target_id=user.id)
    db.commit()
    return result


def get_user_from_access_token(
    db: Session, *, access_token: str, allow_restricted: bool = False
) -> User:
    try:
        payload = decode_access_token(access_token)
        user_id = int(payload["sub"])
        sid = payload.get("sid")
        if not isinstance(sid, str):
            raise ValueError("session required")
    except (InvalidToken, KeyError, ValueError, TypeError) as exc:
        raise InvalidRefreshToken("无效的认证凭证") from exc
    session = db.get(AuthSession, sid, populate_existing=True)
    user = db.get(User, user_id, populate_existing=True)
    if (
        user is None
        or not user.is_active
        or session is None
        or session.user_id != user_id
        or session.is_revoked
        or session.expires_at <= _now()
    ):
        raise InvalidRefreshToken("会话已失效，请重新登录")
    expected = "password_change" if session.restricted else "access"
    if payload.get("purpose") != expected:
        raise InvalidRefreshToken("认证凭证用途不正确")
    if (session.restricted or user.is_default_password) and not allow_restricted:
        raise InvalidRefreshToken("请先修改临时密码")
    db.info["auth_session_id"] = session.id
    return user


# ═══════════════════════════════════════════════════════════════
# User management (superadmin-only flows)
# ═══════════════════════════════════════════════════════════════


def list_users(db: Session) -> list[User]:
    return repo.list_all_users(db)


def create_user(
    db: Session,
    *,
    email: str,
    full_name: str,
    password: str,
    role: str = "employee",
    acting_user_id: int | None = None,
) -> User:
    if acting_user_id is not None:
        authorize_user_management(db, acting_user_id)
    role = canonicalize_role(role)
    if role not in ROLE_LEVELS:
        raise InvalidRole("无效角色")
    error = password_error(password)
    if error:
        raise WeakPassword(error)
    if repo.get_user_by_email(db, email) is not None:
        raise EmailAlreadyExists("邮箱已存在")
    user = User(
        email=email,
        full_name=full_name,
        role=role,
        hashed_password=hash_password(password),
        is_active=True,
        is_default_password=True,
        temporary_password_expires_at=_now() + timedelta(hours=settings.TEMPORARY_PASSWORD_HOURS),
    )
    db.add(user)
    db.flush()
    audit.record(db, "user_created", actor_id=acting_user_id, target_id=user.id, role_after=role)
    db.commit()
    return user


def authorize_user_management(db: Session, acting_user_id: int | None) -> list[User]:
    # Stable ordering serializes competing changes to the last active roots.
    roots = list(
        db.scalars(
            select(User)
            .where(User.role == "superadmin", User.is_active.is_(True))
            .order_by(User.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if acting_user_id is not None:
        actor = get_business_user(db, acting_user_id)
        if actor.role != "superadmin":
            raise InvalidRole("需要超级管理员权限")
        if sid := db.info.get("auth_session_id"):
            assert_session_active(db, acting_user_id, sid)
    return roots


def update_user(
    db: Session,
    *,
    user_id: int,
    full_name: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
    acting_user_id: int | None = None,
) -> User:
    roots = authorize_user_management(db, acting_user_id)
    user = _locked_user(db, user_id)
    next_role = canonicalize_role(role) if role is not None else user.role
    if next_role not in ROLE_LEVELS:
        raise InvalidRole("无效角色")
    removes_root = user.role == "superadmin" and (is_active is False or next_role != "superadmin")
    if acting_user_id == user_id and (is_active is False or next_role != user.role):
        raise CannotDeactivateSelf("请由其他超级管理员完成停用或角色交接")
    if removes_root and user.is_active and len(roots) <= 1:
        raise CannotDeactivateSelf("必须保留至少一名有效超级管理员")
    before_role, before_active = user.role, user.is_active
    user.role = next_role
    if full_name is not None:
        user.full_name = full_name
    if is_active is not None:
        user.is_active = is_active
    if is_active is False:
        _revoke_sessions(db, user.id)
    audit.record(
        db,
        "user_updated",
        actor_id=acting_user_id,
        target_id=user.id,
        role_before=before_role,
        role_after=user.role,
        active_before=before_active,
        active_after=user.is_active,
    )
    db.commit()
    return user


def deactivate_user(db: Session, *, user_id: int, acting_user_id: int) -> None:
    update_user(db, user_id=user_id, is_active=False, acting_user_id=acting_user_id)


def reset_user_password(db: Session, *, user_id: int, acting_user_id: int | None = None) -> str:
    if acting_user_id is not None:
        authorize_user_management(db, acting_user_id)
    user = _locked_user(db, user_id)
    temp_password = secrets.token_urlsafe(18)
    user.hashed_password = hash_password(temp_password)
    user.is_default_password = True
    user.temporary_password_expires_at = _now() + timedelta(hours=settings.TEMPORARY_PASSWORD_HOURS)
    user.password_changed_at = _now()
    user.failed_login_attempts = 0
    user.locked_until = None
    _revoke_sessions(db, user.id)
    audit.record(db, "password_reset", actor_id=acting_user_id, target_id=user.id)
    db.commit()
    return temp_password


def logout_access(db: Session, access_token: str) -> None:
    try:
        user = get_user_from_access_token(db, access_token=access_token, allow_restricted=True)
    except InvalidRefreshToken:
        return
    _locked_user(db, user.id)
    _revoke_sessions(db, user.id, db.info["auth_session_id"])
    audit.record(db, "logout", actor_id=user.id, target_id=user.id)
    db.commit()


def assert_session_active(
    db: Session, user_id: int, session_id: str, *, allow_restricted: bool = False
) -> None:
    session = db.get(AuthSession, session_id, populate_existing=True)
    if (
        session is None
        or session.user_id != user_id
        or session.is_revoked
        or (session.restricted and not allow_restricted)
        or session.expires_at <= _now()
    ):
        raise AccountInactive("会话已失效")
