"""Pydantic DTOs for the document domain.

Shape stays compatible with v2's `routes/documents.py` so the v2 frontend
(`v2-frontend/src/lib/documents-api.ts`) keeps working unchanged.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    # 2026-07-03: documents are company-wide. `uploader_email` /
    # `uploader_name` are display hints for the UI so any employee
    # viewing the list can see who uploaded each doc. Optional so
    # older seeded rows without a valid user don't blow up serialization.
    uploader_email: str | None = None
    uploader_name: str | None = None
    filename: str
    # Optional user-set rename; frontend prefers it when not null.
    display_name: str | None = None
    # P3B (2026-06-21): folder this doc lives in. NULL = root level.
    folder_id: int | None = None
    file_url: str | None = None
    file_type: str
    file_size_bytes: int | None = None
    doc_type: str | None = None
    extraction_method: str | None = None
    status: str
    processing_error: str | None = None
    product_count: int = 0
    linked_order_id: int | None = None
    preview_url: str | None = None
    preview_text: str | None = None
    # Tags + summary populated by the workflow's summarizer step (2026-05-08).
    # tags is a flat list mixing system tags (`file_type:pdf`,
    # `doc_type:purchase_order`, `extractor:pypdfium2`, `has_products:true`,
    # `lang:en`) with LLM topic tags (`beef-supplier`, `celebrity-cruise`).
    tags: list[str] | None = None
    # 2026-05-10: user-managed tags (manually added in the UI). Lives
    # alongside `tags` so re-extracting doesn't wipe the user's labels.
    user_tags: list[str] | None = None
    summary: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    extracted_at: datetime | None = None


class DocumentDetailResponse(DocumentResponse):
    content_markdown: str | None = None
    extracted_data: dict[str, Any] | None = None


class PaginatedDocumentsResponse(BaseModel):
    total: int
    items: list[DocumentResponse]


class DocumentTypeUpdateRequest(BaseModel):
    doc_type: str


class DocumentFolderResponse(BaseModel):
    """A node in the folder tree (P3B 2026-06-21).

    `document_count` is the count of documents directly in THIS folder
    (not including descendants). The frontend can sum descendants if it
    needs total-with-children; we keep the server-side number simple +
    cheap to compute on the list endpoint.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    parent_folder_id: int | None = None
    document_count: int = 0
    # 2026-07-22: user-picked color. Null = fall back to deterministic
    # palette on the frontend. Stored + returned verbatim.
    color: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DocumentFolderCreateRequest(BaseModel):
    name: str
    parent_folder_id: int | None = None
    color: str | None = None


class DocumentFolderUpdateRequest(BaseModel):
    """PATCH a folder.

    `name` renames; `parent_folder_id` re-parents (null = move to root);
    `color` recolors (null = clear back to palette default). All optional;
    absent fields are left alone.
    """

    name: str | None = None
    parent_folder_id: int | None = None
    color: str | None = None


class DocumentMoveRequest(BaseModel):
    """Body for PATCH /api/documents/{id}/folder.

    `folder_id: None` moves the doc back to root.
    """

    folder_id: int | None = None


class DocumentRenameRequest(BaseModel):
    """Body for PATCH /api/documents/{id}/display-name.

    `None` clears the rename and reverts the UI to showing `filename`.
    Trimming + length cap happens in the service layer so a wider call
    site (LINE bot, agent tool) can share the same rules.
    """

    display_name: str | None = None


class DocumentUserTagAddRequest(BaseModel):
    """Body for POST /api/documents/{id}/user-tags — append one tag."""

    tag: str


class DocumentMetadataUpdateRequest(BaseModel):
    fields: dict[str, Any]


class OrderPayloadResponse(BaseModel):
    """Placeholder — populated fully in Phase 3 when Order subtype exists."""

    document_id: int
    doc_type: str | None = None
    order_metadata: dict[str, Any]
    products: list[dict[str, Any]]
    product_count: int
    missing_fields: list[str]
    blocking_missing_fields: list[str]
    field_evidence: dict[str, Any]
    confidence_summary: dict[str, Any]
    ready_for_order_creation: bool


class DocumentCreateOrderRequest(BaseModel):
    force: bool = False
