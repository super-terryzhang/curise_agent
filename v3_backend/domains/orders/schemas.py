"""Pydantic DTOs for the orders domain — matched to v2 frontend expectations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class OrderListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int | None = None
    filename: str
    file_url: str | None = None
    file_type: str | None = "pdf"
    status: str
    processing_error: str | None = None
    order_metadata: dict[str, Any] | None = None
    product_count: int = 0
    total_amount: float | None = None
    match_statistics: dict[str, Any] | None = None
    has_inquiry: bool = False
    is_reviewed: bool = False
    fulfillment_status: str = "pending"
    template_id: int | None = None
    country_name: str | None = None
    template_match_method: str | None = None
    # loading_date — top-level so the list page can show it without
    # digging into order_metadata. Added 2026-06-16 per Felix R6.
    loading_date: str | None = None
    # group_id — R7 / Felix 2026-06-12 display grouping. When non-null,
    # frontend renders this order under OrderGroupsSection instead of
    # the flat table.
    group_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    processed_at: datetime | None = None


class OrderDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    document_id: int | None = None
    filename: str
    file_url: str | None = None
    file_type: str
    status: str
    processing_error: str | None = None
    country_id: int | None = None
    port_id: int | None = None
    delivery_date: str | None = None
    loading_date: str | None = None
    group_id: int | None = None
    extraction_data: dict[str, Any] | None = None
    order_metadata: dict[str, Any] | None = None
    products: list[dict[str, Any]] | None = None
    product_count: int = 0
    total_amount: float | None = None
    match_results: list[dict[str, Any]] | None = None
    match_statistics: dict[str, Any] | None = None
    anomaly_data: dict[str, Any] | None = None
    actionable_count: int = 0
    financial_data: dict[str, Any] | None = None
    inquiry_data: dict[str, Any] | None = None
    is_reviewed: bool = False
    reviewed_at: datetime | None = None
    reviewed_by: int | None = None
    review_notes: str | None = None
    fulfillment_status: str = "pending"
    delivery_data: dict[str, Any] | None = None
    invoice_number: str | None = None
    invoice_amount: float | None = None
    invoice_date: str | None = None
    payment_amount: float | None = None
    payment_date: str | None = None
    payment_reference: str | None = None
    attachments: list[dict[str, Any]] = []
    fulfillment_notes: str | None = None
    delivery_environment: dict[str, Any] | None = None
    template_id: int | None = None
    template_match_method: str | None = None
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None = None


class OrderUpdateRequest(BaseModel):
    """Partial update via PATCH /orders/{id}."""

    po_number: str | None = None
    ship_name: str | None = None
    vendor_name: str | None = None
    delivery_date: str | None = None
    loading_date: str | None = None
    order_date: str | None = None
    currency: str | None = None
    destination_port: str | None = None
    country_id: int | None = None
    port_id: int | None = None
    products: list[dict[str, Any]] | None = None
    order_metadata: dict[str, Any] | None = None


class OrderRematchRequest(BaseModel):
    """POST /orders/{id}/rematch — optional hints."""

    country_id: int | None = None
    port_id: int | None = None
    delivery_date: str | None = None


class OrderReviewRequest(BaseModel):
    notes: str | None = None
