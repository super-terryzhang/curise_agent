"""Pure cryptographic helpers.

This module has NO database dependencies. Business logic that uses refresh
tokens (revoke, rotate, etc.) lives in `domains/identity/service.py`.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from infrastructure.config import settings

# ─── Passwords ───────────────────────────────────────────────

_pwd_ctx = CryptContext(schemes=["bcrypt_sha256", "bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    if len(plain.encode()) > 4096 or (hashed.startswith(("$2a$", "$2b$", "$2y$")) and len(plain.encode()) > 72):
        return False
    try:
        return _pwd_ctx.verify(plain, hashed)
    except (ValueError, TypeError):
        return False


# ─── Access token (JWT) ───────────────────────────────────────


class InvalidToken(Exception):
    """Raised when an access token is invalid, expired, or malformed."""


def create_access_token(
    user_id: int,
    role: str,
    expires_delta: timedelta | None = None,
    *,
    session_id: str | None = None,
    restricted: bool = False,
) -> str:
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload: dict[str, Any] = {
        "exp": expire,
        "iat": datetime.now(UTC),
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "purpose": "password_change" if restricted else "access",
        "sid": session_id,
        "sub": str(user_id),
        "role": role,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM],
                          issuer=settings.JWT_ISSUER, audience=settings.JWT_AUDIENCE,
                          options={"require_exp": True, "require_sub": True, "require_aud": True, "require_iss": True})
    except JWTError as exc:
        raise InvalidToken(str(exc)) from exc


# ─── Refresh token (opaque UUID, hashed at rest) ──────────────


def generate_refresh_token() -> str:
    """Opaque token, not a JWT. Server holds a SHA-256 hash."""
    return uuid.uuid4().hex


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ─── Role hierarchy ───────────────────────────────────────────
#
# Canonical role names. There used to be two aliases — `super_admin`
# (synonym for `superadmin`) and `user` (synonym for `employee`) —
# but having two strings for one role was a latent privilege bug:
# checks like `if user.role == "superadmin"` would silently miss
# accounts created with `super_admin`. TD-4 (2026-06-16) removed the
# aliases. `canonicalize_role()` below still maps legacy values to the
# canonical name so any old payload / DB row gets normalized at the
# service boundary instead of leaking into authorization decisions.

ROLE_LEVELS: dict[str, int] = {
    "superadmin": 100,
    "admin": 50,
    "finance": 30,
    "employee": 20,
}


_ROLE_ALIASES: dict[str, str] = {
    "super_admin": "superadmin",
    "user": "employee",
}


def canonicalize_role(role: str) -> str:
    """Map legacy aliases (`super_admin`, `user`) to the canonical role.

    Apply this at every place that accepts a role from outside the
    process boundary (HTTP body, agent input, DB read of a legacy row,
    JWT claim from an older token). Authorization checks against the
    canonical name then become single-string equality without alias
    branching.
    """
    return _ROLE_ALIASES.get(role, role)


def role_level(role: str) -> int:
    return ROLE_LEVELS.get(canonicalize_role(role), 0)
