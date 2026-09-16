"""ORM tables for the master-data upload pipeline.

Three tables, one row-per-something each:

- `v3_upload_batches`     — one row per uploaded Excel file
- `v3_staging_products`   — one row per data row in the file (parsed + scored)
- `v3_product_changelog`  — one row per field change, written at commit time

Why staging instead of write-then-confirm:

1. Big uploads (500+ rows) need a place to land before the user sees a
   diff. Doing the diff in-memory only would mean re-parsing on every
   preview render.
2. Resolution (code-match / fuzzy-match) takes a non-trivial pass over
   `products`. We don't want to rerun it on every preview either.
3. Rollback needs a record of what changed; writing the changelog at
   commit time gives a clean per-batch audit trail.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.db.base import Base


class UploadBatch(Base):
    """One row per uploaded Excel file."""

    __tablename__ = "v3_upload_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sheet_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    header_row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    header_diagnostics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    entity_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="products"
    )

    # Lifecycle (canonical, post-2026-05-25):
    #   parsing → ready → resolved ─→ completed ─→ rolled_back
    #                            └─→ cancelled
    #   parsing/ready/resolved → failed (error path)
    # `cancelled` = user declined before commit (no changelog needed).
    # `rolled_back` = post-commit undo via changelog.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="parsing")

    # Counters populated by parse / resolve / commit stages
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    parsed_rows: Mapped[int] = mapped_column(Integer, default=0)
    matched_exact: Mapped[int] = mapped_column(Integer, default=0)
    matched_fuzzy: Mapped[int] = mapped_column(Integer, default=0)
    new_rows: Mapped[int] = mapped_column(Integer, default=0)
    error_rows: Mapped[int] = mapped_column(Integer, default=0)
    created_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StagingProduct(Base):
    """One row per data row in the uploaded Excel.

    `raw_data` keeps the original column→value map (for audit / debugging).
    The flattened `product_code` / `product_name` / etc. columns make
    matching queries fast.
    """

    __tablename__ = "v3_staging_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("v3_upload_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # The 1-based row number visible in Excel. `row_index` remains the
    # compact legacy sequence so old agent/artifact consumers keep working.
    source_row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # Normalized for matching
    product_code: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    product_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    supplier_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    pack_size: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Resolution
    match_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unresolved"
    )
    match_target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    validation_errors: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)


class ProductChangeLog(Base):
    """One row per field change. Written at commit time, read at rollback."""

    __tablename__ = "v3_product_changelog"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("v3_upload_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)

    action: Mapped[str] = mapped_column(String(20), nullable=False)  # create|update|delete
    field_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    product_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
