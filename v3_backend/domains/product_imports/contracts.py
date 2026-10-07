"""Stable contracts shared by workbook generation, parsing, validation and HTTP."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkbookField(Contract):
    key: str
    label: str
    field_type: str
    required: bool = False
    option_ids: list[str] = Field(default_factory=list)


class WorkbookManifest(Contract):
    contract_version: Literal[1] = 1
    product_schema_version: int = Field(ge=1)
    generated_at: datetime
    product_fields: list[WorkbookField]
    price_fields: list[WorkbookField]


class ImportIssue(Contract):
    severity: Literal["warning", "block"]
    code: str
    message: str
    sheet: str | None = None
    row: int | None = None
    field: str | None = None
    related_rows: list[tuple[str, int]] = Field(default_factory=list)
    existing_reference: str | None = None


class PreviewRow(Contract):
    id: UUID
    sheet: str
    row: int
    action: Literal["create", "update", "skip", "warning", "block"]
    product_key: str | None = None
    target_id: int | None = None
    normalized_values: dict[str, Any] = Field(default_factory=dict)
    issues: list[ImportIssue] = Field(default_factory=list)


class BatchCounts(Contract):
    create: int = 0
    update: int = 0
    skip: int = 0
    warning: int = 0
    block: int = 0


class BatchPreview(Contract):
    batch_id: UUID
    status: Literal["ready", "failed"]
    counts: BatchCounts
    rows: list[PreviewRow]
    issues: list[ImportIssue] = Field(default_factory=list)


class CommitResult(Contract):
    batch_id: UUID
    status: Literal["committed"] = "committed"
    created: int = 0
    updated: int = 0
    skipped: int = 0


class RollbackResult(Contract):
    batch_id: UUID
    status: Literal["rolled_back"] = "rolled_back"
    restored: int = 0
    archived: int = 0
