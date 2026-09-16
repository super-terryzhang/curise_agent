"""Shared FastAPI dependencies for HTTP routes.

These translate `domains.identity.service` errors into HTTP status codes,
and expose `get_current_user` / `require_role` that all protected routes
depend on.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from domains.identity import service as identity_service
from domains.identity.models import User
from infrastructure.db.session import get_db

bearer_scheme = HTTPBearer(auto_error=False)

DbDep = Annotated[Session, Depends(get_db)]


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbDep,
) -> User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="请先登录", headers={"WWW-Authenticate": "Bearer"})
    try:
        return identity_service.get_user_from_access_token(db, access_token=credentials.credentials)
    except identity_service.InvalidRefreshToken as exc:
        # identity_service uses InvalidRefreshToken for both access and refresh token
        # failures; 401 is the correct status for either.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


def get_authenticated_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)], db: DbDep,
) -> User:
    """Only auth/me and change-password accept a restricted session."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="请先登录", headers={"WWW-Authenticate": "Bearer"})
    try:
        return identity_service.get_user_from_access_token(db, access_token=credentials.credentials, allow_restricted=True)
    except identity_service.InvalidRefreshToken as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


AuthenticatedUser = Annotated[User, Depends(get_authenticated_user)]
CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*allowed_roles: str) -> Callable[[User], User]:
    """Dependency factory: ensure current user has one of the given roles."""

    def checker(user: CurrentUser) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        return user

    return checker


# Pre-built role guards for common cases.
# `finance` is included in `require_writer` because finance users (JP staff per
# 2026-06-08 product decision) own the order FinancialTab (PnL, cost items,
# tax rate) and also need full employee write access for day-to-day order work.
# Financial endpoints in `order_financials.py` use `Writer` too, so adding
# finance here grants both surfaces in one place.
# See docs/current_progress/2026-06-16/R3_finance_role.md for the audit + decision.
require_writer = require_role("superadmin", "admin", "employee", "finance")
require_admin = require_role("superadmin", "admin")
require_superadmin = require_role("superadmin")
require_product_uploader = require_role("superadmin", "admin", "employee")

# Type aliases so route signatures read `admin: Superadmin` etc.
Writer = Annotated[User, Depends(require_writer)]
Admin = Annotated[User, Depends(require_admin)]
Superadmin = Annotated[User, Depends(require_superadmin)]
ProductUploader = Annotated[User, Depends(require_product_uploader)]


def require_capability(capability: str) -> Callable[[User, Session], User]:
    """Dependency factory: white-list per-user feature access.

    superadmin auto-passes every capability check (root bypass). All
    other roles must have an explicit row in `v3_user_capabilities` for
    the given key.

    `capability` MUST be a key from `infrastructure.capabilities`; we
    don't enforce that at definition time so endpoints can wire up a new
    capability ahead of the catalog change in the same PR. If a typo
    sneaks in, no user can ever match it → permanent 403, which surfaces
    in QA immediately.
    """
    from sqlalchemy import select

    from domains.identity.models import UserCapability

    def checker(user: CurrentUser, db: DbDep) -> User:
        if user.role == "superadmin":
            return user
        row = db.execute(
            select(UserCapability)
            .where(UserCapability.user_id == user.id)
            .where(UserCapability.capability == capability)
        ).first()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要 {capability} 权限，请联系管理员授权",
            )
        return user

    return checker
