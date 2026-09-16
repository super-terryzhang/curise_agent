"""Stable HTTP DTOs for the deterministic product-upload workbench."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class HeaderColumnDiagnostic(BaseModel):
    column: int
    raw: str
    canonical: str | None
    status: Literal["recognized", "unrecognized"]


class HeaderIssue(BaseModel):
    code: str
    field: str | None = None
    columns: list[int] = Field(default_factory=list)
    message: str


class HeaderDiagnostics(BaseModel):
    columns: list[HeaderColumnDiagnostic] = Field(default_factory=list)
    unrecognized: list[HeaderColumnDiagnostic] = Field(default_factory=list)
    duplicate_canonical: list[dict[str, Any]] = Field(default_factory=list)
    missing_required: list[str] = Field(default_factory=list)
    blocking_issues: list[HeaderIssue] = Field(default_factory=list)


class UploadSummary(BaseModel):
    create: int
    update: int
    skip: int
    error: int
    total: int


class WorkflowBatch(BaseModel):
    id: int
    filename: str
    file_sha256: str | None
    original_available: bool
    sheet_name: str | None
    header_row_number: int | None
    workflow_version: int
    status: str
    current_step: int
    total_rows: int
    header_diagnostics: HeaderDiagnostics
    summary: UploadSummary
    can_continue: bool
    created_at: str | None
    committed_at: str | None


class WorkflowBatchPage(BaseModel):
    items: list[WorkflowBatch]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class RowIdentity(BaseModel):
    product_id: int | None
    product_code: str | None
    product_name: str | None


class FieldChange(BaseModel):
    key: str
    label: str
    before: Any = None
    after: Any = None
    action: str | None = None
    changed: bool
    currency: str | None = None


class RowIssue(BaseModel):
    code: str
    field: str | None = None
    message: str


class WorkflowRow(BaseModel):
    staging_id: int
    source_row_number: int
    kind: Literal["error", "create", "update", "skip"]
    identity: RowIdentity
    fields: list[FieldChange]
    issues: list[RowIssue]
    reason: str | None = None


class WorkflowRowsPage(BaseModel):
    items: list[WorkflowRow]
    page: int
    page_size: int
    total_items: int
    total_pages: int
    summary: UploadSummary


class CommitResult(BaseModel):
    created: int
    updated: int
    skipped: int
    errors: int
    error_details: list[dict[str, Any]] = Field(default_factory=list)


class CancelResult(BaseModel):
    ok: bool
    batch_id: int
    status: str
    already: bool
