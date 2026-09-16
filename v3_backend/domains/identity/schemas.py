"""Pydantic DTOs for the identity domain.

These are the wire-level shapes for HTTP requests/responses. They are also
returned by `service.py` so the HTTP layer does no conversion.

Compatibility: field names and types must match v2's `core/schemas.py` exactly.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=4096)


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(max_length=512)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=4096)
    new_password: str = Field(max_length=4096)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str | None = None
    role: str
    is_active: bool
    is_default_password: bool = False
    # White-list capability keys granted to this user. Empty when the
    # user has no per-feature grants. superadmin always sees an empty
    # list here (root bypass happens server-side in `require_capability`)
    # — the frontend treats `role === "superadmin"` as "all caps".
    capabilities: list[str] = []


class UserListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str | None = None
    role: str
    is_active: bool
    is_default_password: bool = False
    last_login: datetime | None = None
    created_at: datetime | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str = ""
    token_type: str = "bearer"
    user: UserResponse


class UserCreateRequest(BaseModel):
    email: str = Field(max_length=320)
    full_name: str
    role: str = "employee"
    password: str = Field(min_length=15, max_length=4096)


class UserUpdateRequest(BaseModel):
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
