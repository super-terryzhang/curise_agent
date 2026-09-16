"""Pydantic schemas for the LINE domain.

These are wire-format DTOs — used by `apps.http.line_bind` for request /
response shapes. Internal service functions deal in plain dicts / typed
arguments, not these.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class BindRequest(BaseModel):
    """POST /api/line/bind/{token} — user proves identity to consume the bind token."""

    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=1, max_length=200)


class BindResponse(BaseModel):
    """Returned on successful bind."""

    line_user_id: str
    user_id: int
    user_email: str
    user_role: str
