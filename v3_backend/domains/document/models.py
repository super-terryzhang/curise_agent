"""Document ORM — parent type for every uploaded file (ADR-0001).

Stores only what's common to ALL document types:
- File metadata: filename, file_url, file_type, file_size_bytes
- Universal extraction output: extracted_blocks JSON, content_markdown
- Pipeline status: status, extraction_method, processing_error, timestamps
- Classification result: doc_type (discriminator for Phase 3+ subtypes)

Does NOT store PO-specific fields (po_number, ship_name, products). Those
belong on `Order` and sibling subtype tables — see `domains/orders/models.py`
(Phase 3).

Table name `v2_documents` is kept to share rows with the production v2 DB.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.db.base import Base


class DocumentFolder(Base):
    """User-owned hierarchical folder for organizing Documents (P3B 2026-06-21).

    Coexists with `Document.user_tags` rather than replacing them:
        - folder = the document's *primary location* (Dropbox-style)
        - tags = orthogonal cross-cutting labels (a doc can be in folder
          "Celebrity/2026-06" AND tagged "completed"; tag filter still
          works across folders)

    Self-referential tree via `parent_folder_id` — NULL means root.
    Cycle prevention is enforced in the service layer (no DB constraint
    can express "no path back to self" cheaply on SQLite/Postgres without
    a CTE check). Cross-user isolation: every CRUD goes through
    `user_id` filter so a folder ID leak still can't be exploited.
    """

    __tablename__ = "v3_document_folders"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Self-ref FK on the same table. NULL = root-level folder. ON DELETE
    # is enforced in the service (we want "you must move/delete children
    # first" UX, not silent cascade). Index speeds up tree queries.
    parent_folder_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("v3_document_folders.id"),
        nullable=True,
        index=True,
    )
    # User-picked color (2026-07-22). NULL = fall back to the deterministic
    # frontend palette (id % 12). Stored as a hex string (`#3b82f6`) or a
    # named palette key; validation lives in the service layer.
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Document(Base):
    __tablename__ = "v2_documents"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    # 2026-06-21: optional user-set rename. `filename` itself is immutable
    # because it's tied to the storage key + audit trail; `display_name`
    # is the preferred label the UI shows when present. NULL → fall back
    # to `filename` so old rows render unchanged.
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False, default="pdf")
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 2026-06-21 P3B: folder this document lives in. NULL = root level.
    # Coexists with `user_tags` (folders = primary location, tags =
    # cross-cutting labels).
    folder_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("v3_document_folders.id"),
        nullable=True,
        index=True,
    )

    doc_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    content_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # 2026-05-08: tags + summary added for human/AI-driven document search.
    # `tags` mixes system tags (`file_type:pdf`, `doc_type:purchase_order`,
    # `extractor:pypdfium2`, `lang:zh`, `has_products:true`) with LLM-generated
    # topic tags (`beef-supplier`, `celebrity-cruise`, …). `summary` is a 2-3
    # paragraph LLM-generated abstract. See `domains/document/summarizer.py`.
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 2026-05-10: user-managed tags. Lives alongside `tags` so re-extracting
    # the document (which rebuilds `tags`) doesn't wipe the user's manual
    # annotations. Each tag is normalized to lowercase + kebab-case.
    user_tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="uploaded")
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
