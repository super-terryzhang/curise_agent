"""Pydantic shapes for the financials API surface."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CostItemCreate(BaseModel):
    """POST body for adding a cost item."""

    category: str = Field(..., min_length=1, max_length=50)
    amount: Decimal
    currency: str = Field(..., min_length=1, max_length=10)
    notes: str | None = None


class CostItemUpdate(BaseModel):
    """PATCH body. All fields optional — only present keys are updated."""

    category: str | None = Field(default=None, min_length=1, max_length=50)
    amount: Decimal | None = None
    currency: str | None = Field(default=None, min_length=1, max_length=10)
    notes: str | None = None


class FinancialSettingsUpdate(BaseModel):
    """PATCH body for per-order display_currency + tax_rate."""

    display_currency: str | None = Field(default=None, min_length=1, max_length=10)
    # Pydantic enforces range 0–1 (10000% would be absurd; this also
    # catches typo like 6 vs 0.06 at the API layer).
    tax_rate: Decimal | None = Field(default=None, ge=0, le=1)


class CostItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    category: str
    amount: Decimal
    currency: str
    notes: str | None = None
