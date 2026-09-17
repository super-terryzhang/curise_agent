"""ORM models for bulk image upload (migration 0021).

See migration docstring for the lifecycle + design rationale.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.db.base import Base


class BulkImageBatch(Base):
    """One row per ZIP upload — the batch envelope.

    Status state machine:
        uploading      → newly created, ZIP is in flight to GCS
        preview_ready  → parsed; user can review matches before commit
        processing     → background commit job running
        completed      → ingestion done; staging rows + ZIP can be cleaned
        cancelled      → user backed out before commit
        error          → parse / validation failed; see error_message
    """

    __tablename__ = "v3_bulk_image_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    zip_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    zip_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False, default="zip")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="uploading")
    total_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unmatched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ingested_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    # Heartbeat — bumped on every row change (SQLAlchemy `onupdate`)
    # PLUS explicit ticks inside the commit loop every 5 rows so the
    # GC sweep can tell "live worker mid-run" from "died worker" via
    # a 15-minute staleness threshold (see migration 0022 docstring).
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class BulkImageStaging(Base):
    """One row per image file inside the uploaded ZIP — the parsed manifest.

    Lives only until the parent BulkImageBatch is `completed` or
    `cancelled`. Status mirrors the per-row outcome:
        matched         → identity resolved to a Product row
        unmatched       → path didn't resolve (country/port/code typo)
        error           → file rejected (size, mime, > MAX_IMAGES)
        committed       → real ProductImage row written
        committed_failed → ingestion attempted but failed at commit time
    """

    __tablename__ = "v3_bulk_image_staging"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("v3_bulk_image_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    zip_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    country_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    port_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    product_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    image_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="matched")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    preview_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    issue_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    upload_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    decision: Mapped[str] = mapped_column(String(20), nullable=False, default="include")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    committed_image_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("v3_product_images.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class BulkImageProductPlan(Base):
    """Persisted final image order for one product in a direct batch."""

    __tablename__ = "v3_bulk_image_product_plans"
    __table_args__ = (
        UniqueConstraint("batch_id", "product_id", name="uq_bulk_image_plan_batch_product"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("v3_bulk_image_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expected_existing_image_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    ordered_items: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
