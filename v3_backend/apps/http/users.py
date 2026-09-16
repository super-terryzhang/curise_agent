"""User-management HTTP endpoints (superadmin only).

URL space `/api/users/*` — compatible with v2's `routes/users.py`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from apps.http._deps import DbDep, Superadmin
from apps.http.auth_limits import auth_rate_limit
from domains.identity import audit
from domains.identity import service as identity_service
from domains.identity.schemas import (
    UserCreateRequest,
    UserListResponse,
    UserUpdateRequest,
)

router = APIRouter(dependencies=[Depends(auth_rate_limit)], prefix="/users", tags=["users"])


def _translate(exc: identity_service.AuthError) -> HTTPException:
    if isinstance(exc, identity_service.AccountInactive):
        return HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))
    if isinstance(exc, identity_service.UserNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, identity_service.EmailAlreadyExists):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(
        exc,
        identity_service.InvalidRole
        | identity_service.CannotDeactivateSelf
        | identity_service.WeakPassword,
    ):
        return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))


@router.get("", response_model=list[UserListResponse])
def list_users(db: DbDep, _admin: Superadmin) -> list[UserListResponse]:
    return [UserListResponse.model_validate(u) for u in identity_service.list_users(db)]


@router.post("", response_model=UserListResponse, status_code=201)
def create_user(body: UserCreateRequest, db: DbDep, _admin: Superadmin) -> UserListResponse:
    try:
        user = identity_service.create_user(
            db,
            email=body.email,
            full_name=body.full_name,
            password=body.password,
            role=body.role,
            acting_user_id=_admin.id,
        )
    except identity_service.AuthError as exc:
        raise _translate(exc) from exc
    return UserListResponse.model_validate(user)


@router.patch("/{user_id}", response_model=UserListResponse)
def update_user(
    user_id: int,
    body: UserUpdateRequest,
    db: DbDep,
    _admin: Superadmin,
) -> UserListResponse:
    try:
        user = identity_service.update_user(
            db,
            user_id=user_id,
            full_name=body.full_name,
            role=body.role,
            is_active=body.is_active,
            acting_user_id=_admin.id,
        )
    except identity_service.AuthError as exc:
        raise _translate(exc) from exc
    return UserListResponse.model_validate(user)


@router.delete("/{user_id}")
def deactivate_user(
    user_id: int,
    db: DbDep,
    admin: Superadmin,
) -> dict[str, str]:
    try:
        identity_service.deactivate_user(db, user_id=user_id, acting_user_id=admin.id)
    except identity_service.AuthError as exc:
        raise _translate(exc) from exc
    return {"detail": "用户已停用"}


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: int,
    db: DbDep,
    _admin: Superadmin,
) -> dict[str, str]:
    try:
        temp_password = identity_service.reset_user_password(
            db, user_id=user_id, acting_user_id=_admin.id
        )
    except identity_service.AuthError as exc:
        raise _translate(exc) from exc
    return {"detail": f"密码已重置为临时密码: {temp_password}"}


# ─── Capabilities — per-user white-list ACL ────────────────────
#
# All three endpoints are superadmin-only. Capability state is read
# (1) by this admin UI, and (2) by the user's own `/auth/me` so the
# frontend can conditionally render gated features (financial tab etc.).
# Validation: only catalog keys are accepted on POST — typo'd caps would
# silently 403 forever, so we reject them at the boundary.


@router.get("/{user_id}/capabilities")
def list_user_capabilities(user_id: int, db: DbDep, _admin: Superadmin) -> list[str]:
    from sqlalchemy import select

    from domains.identity.models import User, UserCapability

    if db.get(User, user_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    rows = db.execute(
        select(UserCapability.capability).where(UserCapability.user_id == user_id)
    ).all()
    return [r[0] for r in rows]


@router.post("/{user_id}/capabilities/{capability}", status_code=201)
def grant_capability(
    user_id: int,
    capability: str,
    db: DbDep,
    admin: Superadmin,
) -> dict[str, str]:
    from datetime import datetime

    from domains.identity.models import User, UserCapability
    from infrastructure.capabilities import is_known_capability

    try:
        identity_service.authorize_user_management(db, admin.id)
    except identity_service.AuthError as exc:
        raise _translate(exc) from exc
    if not is_known_capability(capability):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"未知的 capability key: {capability}",
        )
    if db.get(User, user_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    existing = db.get(UserCapability, (user_id, capability))
    if existing is not None:
        # Idempotent — already granted is success, not conflict.
        return {"detail": "已授权"}
    db.add(
        UserCapability(
            user_id=user_id,
            capability=capability,
            granted_by_user_id=admin.id,
            granted_at=datetime.utcnow(),
        )
    )
    audit.record(
        db, "capability_granted", actor_id=admin.id, target_id=user_id, capability=capability
    )
    db.commit()
    return {"detail": "已授权"}


@router.delete("/{user_id}/capabilities/{capability}")
def revoke_capability(
    user_id: int,
    capability: str,
    db: DbDep,
    _admin: Superadmin,
) -> dict[str, str]:
    from domains.identity.models import UserCapability

    try:
        identity_service.authorize_user_management(db, _admin.id)
    except identity_service.AuthError as exc:
        raise _translate(exc) from exc
    row = db.get(UserCapability, (user_id, capability))
    if row is None:
        # Idempotent — revoking a non-existent grant returns success
        # so double-clicks / network retries don't 404.
        return {"detail": "已撤销"}
    db.delete(row)
    audit.record(
        db, "capability_revoked", actor_id=_admin.id, target_id=user_id, capability=capability
    )
    db.commit()
    return {"detail": "已撤销"}


@router.get("/_capabilities/catalog")
def get_capability_catalog(_admin: Superadmin) -> list[dict[str, str]]:
    """List every capability key the system knows about. Used by the
    user-management UI to render the checkbox picker — labels come from
    the catalog, not hard-coded in the frontend, so adding a capability
    needs zero frontend code."""
    from infrastructure.capabilities import CAPABILITY_CATALOG

    return [
        {"key": c.key, "label": c.label, "description": c.description}
        for c in CAPABILITY_CATALOG.values()
    ]
