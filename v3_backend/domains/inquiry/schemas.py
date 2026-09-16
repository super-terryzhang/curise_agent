"""Pydantic DTOs for inquiry domain (Phase 4 SupplierTemplate + Phase 5 Inquiry)."""

from __future__ import annotations

import posixpath
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class SupplierTemplateCreate(BaseModel):
    supplier_id: int | None = None
    supplier_ids: list[int] | None = None
    country_id: int | None = None
    template_name: str
    template_file_url: str | None = None
    field_positions: dict[str, Any] | None = None
    has_product_table: bool = True
    product_table_config: dict[str, Any] | None = None
    order_format_template_id: int | None = None
    field_mapping_metadata: dict[str, Any] | None = None
    template_styles: dict[str, Any] | None = None


class SupplierTemplateUpdate(BaseModel):
    template_name: str | None = None
    country_id: int | None = None
    template_file_url: str | None = None
    field_positions: dict[str, Any] | None = None
    has_product_table: bool | None = None
    product_table_config: dict[str, Any] | None = None
    order_format_template_id: int | None = None
    field_mapping_metadata: dict[str, Any] | None = None
    template_styles: dict[str, Any] | None = None
    supplier_ids: list[int] | None = None


class SupplierTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    supplier_id: int | None = None
    supplier_ids: list[int] | None = None
    country_id: int | None = None
    template_name: str
    template_file_url: str | None = None
    field_positions: dict[str, Any] | None = None
    has_product_table: bool
    product_table_config: dict[str, Any] | None = None
    order_format_template_id: int | None = None
    field_mapping_metadata: dict[str, Any] | None = None
    template_styles: dict[str, Any] | None = None
    created_by: int | None = None
    created_at: datetime
    updated_at: datetime


# ─── Phase 5 ──────────────────────────────────────────────────


class TemplateBinding(BaseModel):
    """Template choice for one supplier — used in pre-analysis + per-supplier."""

    id: int | None = None
    name: str | None = None
    method: str = "unavailable"  # exact | candidate_auto | user_selected | candidates | unavailable
    candidates: list[dict[str, Any]] | None = None


class InquirySupplierState(BaseModel):
    """Per-supplier state — used as both pre-analysis row and final result row."""

    model_config = ConfigDict(from_attributes=True)

    supplier_id: int
    supplier_name: str | None = None
    supplier_info: dict[str, Any] | None = None
    product_count: int = 0
    subtotal: float | None = None
    currency: str | None = None
    template: TemplateBinding | None = None
    status: str = "pending"
    excel_file_url: str | None = None
    preview_html_url: str | None = None
    verify_results: list[dict[str, Any]] | None = None
    missing_fields: list[str] | None = None
    elapsed_seconds: float | None = None
    error_message: str | None = None


class InquiryState(BaseModel):
    """Top-level inquiry state — serializes to v2-compatible dict via `to_legacy_dict()`."""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    order_id: int
    group_id: int | None = None
    version: int = 1
    status: str = "pending"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    heartbeat_at: datetime | None = None
    next_retry_at: datetime | None = None
    run_attempts: int = 0
    max_attempts: int = 3
    supplier_count: int = 0
    unassigned_count: int = 0
    total_elapsed_seconds: float | None = None
    member_snapshot: list[dict[str, Any]] | None = None
    unmatched_items: list[dict[str, Any]] | None = None
    error_message: str | None = None
    suppliers: list[InquirySupplierState] = []

    def to_legacy_dict(self) -> dict[str, Any]:
        """Serialize to the v2 `inquiry_data` JSON shape consumed by the frontend.

        v2 shape:
            {
              "status": "...",
              "supplier_count": int,
              "unassigned_count": int,
              "total_elapsed_seconds": float,
              "suppliers": {"<sid>": {...}},
              "generated_files": [{...}]
            }
        """
        suppliers_dict: dict[str, dict[str, Any]] = {}
        generated_files: list[dict[str, Any]] = []
        for s in self.suppliers:
            tpl = s.template
            template_dict = (
                {
                    "id": tpl.id,
                    "name": tpl.name,
                    # Both keys carry the same value — `selection_method` is the
                    # legacy v2 name, `method` is what the v3 frontend types
                    # (`SupplierReadiness.template.method`) actually read. Keep
                    # both until v2 callers are gone.
                    "selection_method": tpl.method,
                    "method": tpl.method,
                }
                if tpl is not None
                else {
                    "id": None,
                    "name": None,
                    "selection_method": "unavailable",
                    "method": "unavailable",
                }
            )
            # `supplier_info` is the existing JSON bag containing
            # {contact, email, phone, country_name, letterhead}. Pull
            # `letterhead` out to a stable top-level key so the frontend
            # has a typed contract instead of digging through a free-
            # form dict. The nested copy in `supplier_info` is left in
            # place — harmless, and any legacy reader keeps working.
            info = s.supplier_info or {}
            letterhead = info.get("letterhead") if isinstance(info, dict) else None
            row: dict[str, Any] = {
                "status": s.status,
                # `gen_status` mirrors `status` for v3 frontend's status pill
                # logic (page.tsx checks `gen_status === "completed"` etc.).
                # `status` stays as the legacy v2 field.
                "gen_status": s.status,
                "supplier_name": s.supplier_name or f"供应商 #{s.supplier_id}",
                "supplier_info": info,
                "supplier_letterhead": letterhead if isinstance(letterhead, list) else [],
                "product_count": s.product_count,
                "subtotal": s.subtotal,
                "currency": s.currency or "",
                "template": template_dict,
                "verify_results": s.verify_results or [],
                "elapsed_seconds": s.elapsed_seconds,
                "missing_fields": s.missing_fields,
            }
            if s.excel_file_url:
                # `filename` = basename of the storage key. The download API
                # uses this as the path slug so the storage layout never leaks
                # to the client.
                filename = posixpath.basename(s.excel_file_url)
                file_info = {
                    "supplier_id": s.supplier_id,
                    "filename": filename,
                    "file_url": s.excel_file_url,
                    "preview_url": s.preview_html_url,
                    "product_count": s.product_count,
                    # Legacy keys preserved for any v2 caller still reading them.
                    "url": s.excel_file_url,
                    "preview_html_url": s.preview_html_url,
                }
                row["file"] = file_info
                generated_files.append(file_info)
            if s.error_message:
                row["error"] = s.error_message
            suppliers_dict[str(s.supplier_id)] = row
        return {
            "inquiry_id": self.id,
            "group_id": self.group_id,
            "version": self.version,
            "status": self.status,
            "supplier_count": self.supplier_count,
            "unassigned_count": self.unassigned_count,
            "total_elapsed_seconds": self.total_elapsed_seconds,
            "heartbeat_at": self.heartbeat_at,
            "next_retry_at": self.next_retry_at,
            "run_attempts": self.run_attempts,
            "max_attempts": self.max_attempts,
            "member_snapshot": self.member_snapshot or [],
            "unmatched_items": self.unmatched_items or [],
            "error_message": self.error_message,
            "suppliers": suppliers_dict,
            "generated_files": generated_files,
        }
