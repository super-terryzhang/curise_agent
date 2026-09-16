"""Pydantic DTOs for the order-groups subdomain (R7)."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class OrderGroupCreate(BaseModel):
    """POST /api/order-groups body."""

    name: str = Field(min_length=1, max_length=200)
    ship_name: str | None = Field(default=None, max_length=200)
    loading_date: str | None = Field(default=None, max_length=50)
    # Initial set of orders to assign. Optional — the frontend can also
    # use POST /{id}/orders later to incrementally add.
    order_ids: list[int] | None = None


class OrderGroupUpdate(BaseModel):
    """PATCH /api/order-groups/{id} body."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    ship_name: str | None = Field(default=None, max_length=200)
    loading_date: str | None = Field(default=None, max_length=50)


class OrderGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    ship_name: str | None = None
    loading_date: str | None = None
    order_count: int = 0
    can_manage: bool = True
    can_generate_inquiry: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OrderGroupAssignBody(BaseModel):
    """POST /api/order-groups/{id}/orders body."""

    order_ids: list[int] = Field(min_length=1)


class SupplyArrangementUpdate(BaseModel):
    """Business fields shared by every PO in one supply arrangement."""

    ship_name: str = Field(min_length=1, max_length=200)
    loading_date: date
    port_id: int = Field(gt=0)
