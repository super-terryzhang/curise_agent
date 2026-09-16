"""Authentication endpoints — thin HTTP adapters over `domains.identity.service`.

URLs match v2 exactly: `/api/auth/*` (router prefix `/auth`, app prefix `/api`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from apps.http._deps import AuthenticatedUser, DbDep, Superadmin
from apps.http.auth_limits import auth_rate_limit
from domains.identity import service as identity_service
from domains.identity.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshTokenRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(dependencies=[Depends(auth_rate_limit)], prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: DbDep) -> TokenResponse:
    try:
        return identity_service.login(db, email=body.email, password=body.password)
    except (
        identity_service.AccountInactive,
        identity_service.AccountLocked,
        identity_service.InvalidCredentials,
    ) as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "邮箱或密码错误，或账号暂时不可用"
        ) from exc


@router.get("/me", response_model=UserResponse)
def me(current_user: AuthenticatedUser, db: DbDep) -> UserResponse:
    return identity_service._serialize_user(db, current_user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshTokenRequest, db: DbDep) -> TokenResponse:
    try:
        return identity_service.refresh_tokens(db, refresh_token=body.refresh_token)
    except identity_service.InvalidRefreshToken as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc


@router.post("/logout")
def logout(body: RefreshTokenRequest, db: DbDep, request: Request) -> dict[str, str]:
    identity_service.logout(db, refresh_token=body.refresh_token)
    bearer = request.headers.get("authorization", "")
    if bearer.startswith("Bearer "):
        identity_service.logout_access(db, bearer[7:])
    return {"detail": "已登出"}


@router.post("/change-password", response_model=TokenResponse)
def change_password(
    body: ChangePasswordRequest,
    current_user: AuthenticatedUser,
    db: DbDep,
) -> TokenResponse:
    try:
        return identity_service.change_password(
            db,
            user=current_user,
            current_password=body.current_password,
            new_password=body.new_password,
        )
    except identity_service.WrongCurrentPassword as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except identity_service.WeakPassword as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except identity_service.AccountInactive as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc


@router.get("/security-events")
def security_events(db: DbDep, _admin: Superadmin, limit: int = 50) -> list[dict]:
    from sqlalchemy import select

    from domains.identity.models import SecurityEvent

    rows = db.scalars(
        select(SecurityEvent)
        .order_by(SecurityEvent.occurred_at.desc())
        .limit(max(1, min(limit, 100)))
    )
    return [
        {
            "id": r.id,
            "occurred_at": r.occurred_at,
            "event_type": r.event_type,
            "actor_id": r.actor_id,
            "target_id": r.target_id,
            "outcome": r.outcome,
            "request_id": r.request_id,
            "channel": r.channel,
            "details": r.details,
        }
        for r in rows
    ]
