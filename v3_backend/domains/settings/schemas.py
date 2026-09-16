"""Pydantic DTOs for the settings domain — match v2 frontend exactly."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

# ─── Field Definition ────────────────────────────────────────────


class FieldDefinitionCreate(BaseModel):
    field_key: str
    field_label: str
    field_type: str = "string"
    is_core: bool = False
    is_required: bool = False
    extraction_hint: str | None = None
    sort_order: int = 0


class FieldDefinitionUpdate(BaseModel):
    field_label: str | None = None
    field_type: str | None = None
    is_required: bool | None = None
    extraction_hint: str | None = None
    sort_order: int | None = None


class FieldDefinitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    schema_id: int
    field_key: str
    field_label: str
    field_type: str
    is_core: bool
    is_required: bool
    extraction_hint: str | None = None
    sort_order: int


# ─── Field Schema ───────────────────────────────────────────────


class FieldSchemaCreate(BaseModel):
    name: str
    description: str | None = None


class FieldSchemaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    is_default: bool
    created_by: int | None = None
    created_at: datetime
    updated_at: datetime
    definitions: list[FieldDefinitionResponse] = []


# ─── Order Format Template ──────────────────────────────────────


class OrderFormatTemplateCreate(BaseModel):
    name: str
    file_type: str = "excel"
    header_row: int = 1
    data_start_row: int = 2
    column_mapping: dict[str, str] | None = None
    field_schema_id: int | None = None
    format_fingerprint: str | None = None
    sample_file_url: str | None = None
    layout_prompt: str | None = None
    extracted_fields: list[dict[str, Any]] | None = None
    source_company: str | None = None
    match_keywords: list[str] | None = None
    notes: str | None = None
    document_schema: dict[str, Any] | None = None
    is_active: bool = True


class OrderFormatTemplateUpdate(BaseModel):
    name: str | None = None
    file_type: str | None = None
    header_row: int | None = None
    data_start_row: int | None = None
    column_mapping: dict[str, str] | None = None
    field_schema_id: int | None = None
    layout_prompt: str | None = None
    extracted_fields: list[dict[str, Any]] | None = None
    source_company: str | None = None
    match_keywords: list[str] | None = None
    notes: str | None = None
    document_schema: dict[str, Any] | None = None
    is_active: bool | None = None


class OrderFormatTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    file_type: str = "excel"
    format_fingerprint: str | None = None
    header_row: int
    data_start_row: int
    column_mapping: dict[str, str] | None = None
    field_schema_id: int | None = None
    sample_file_url: str | None = None
    layout_prompt: str | None = None
    extracted_fields: list[dict[str, Any]] | None = None
    source_company: str | None = None
    match_keywords: list[str] | None = None
    notes: str | None = None
    document_schema: dict[str, Any] | None = None
    is_active: bool = True
    created_by: int | None = None
    created_at: datetime
    updated_at: datetime


# SupplierInfoUpdate / SupplierInfoResponse removed 2026-05-28 — supplier
# info edits consolidated into /api/data/suppliers/{id}. See
# apps/http/settings.py module docstring for rationale.


# ─── Delivery Location ──────────────────────────────────────────


class DeliveryLocationCreate(BaseModel):
    port_id: int | None = None
    name: str
    address: str | None = None
    contact_person: str | None = None
    contact_phone: str | None = None
    delivery_notes: str | None = None
    ship_name_label: str | None = None
    is_default: bool = True


class DeliveryLocationUpdate(BaseModel):
    port_id: int | None = None
    name: str | None = None
    address: str | None = None
    contact_person: str | None = None
    contact_phone: str | None = None
    delivery_notes: str | None = None
    ship_name_label: str | None = None
    is_default: bool | None = None


class DeliveryLocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    port_id: int | None = None
    port_name: str | None = None
    name: str
    address: str | None = None
    contact_person: str | None = None
    contact_phone: str | None = None
    delivery_notes: str | None = None
    ship_name_label: str | None = None
    is_default: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ─── Company Config ─────────────────────────────────────────────


class CompanyConfigItem(BaseModel):
    key: str
    value: str
    label: str | None = None


class CompanyConfigUpdate(BaseModel):
    items: list[CompanyConfigItem]


class CompanyConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    value: str
    label: str | None = None
    sort_order: int = 0
    updated_at: datetime | None = None


# ─── Template analysis (Phase 4 simplified, Phase 5 plugs in Gemini) ──


class TemplateInferRequest(BaseModel):
    raw_text: str = ""
    headers: list[str] = []
    file_type: str = "excel"


class TemplateInferResponse(BaseModel):
    name: str
    source_company: str | None = None
    match_keywords: list[str] = []
    notes: str = ""


class PdfAnalysisResponse(BaseModel):
    document_schema: dict[str, Any]
    document_type: str
    sample_file_url: str
    timing: dict[str, Any] = {}


class SupplierTemplateAnalyzeResponse(BaseModel):
    field_positions: dict[str, Any] = {}
    product_table_config: dict[str, Any] = {}
    cell_map: dict[str, Any] = {}
    template_styles: dict[str, Any] | None = None
    notes: str = ""
    file_url: str
    template_html: str | None = None
    field_mapping_preview: list[dict[str, Any]] | None = None
    order_template_name: str | None = None
