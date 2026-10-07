"""Persistent staging and audit records for the temporary product importer."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.db.base import Base

JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")


def utc_now() -> datetime:
    return datetime.now(UTC)


class ImportBatch(Base):
    __tablename__ = "v3_import_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('uploaded','validating','ready','committed','failed','cancelled','rolled_back')",
            name="ck_import_batch_status",
        ),
        CheckConstraint("contract_version >= 1", name="ck_import_batch_contract_version"),
        CheckConstraint(
            "product_schema_version >= 1", name="ck_import_batch_product_schema_version"
        ),
        CheckConstraint("length(file_sha256) = 64", name="ck_import_batch_file_sha256"),
        Index("ix_import_batches_user_created", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_url: Mapped[str | None] = mapped_column(String(500))
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    target_table: Mapped[str] = mapped_column(String(30), nullable=False, default="products")
    contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    product_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    field_manifest: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="uploaded")
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    create_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    update_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skip_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    block_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON_VALUE)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportRow(Base):
    __tablename__ = "v3_import_rows"
    __table_args__ = (
        UniqueConstraint(
            "batch_id", "sheet_key", "source_row_number", name="uq_import_row_source"
        ),
        CheckConstraint("sheet_key IN ('products','prices')", name="ck_import_row_sheet"),
        CheckConstraint("source_row_number >= 2", name="ck_import_row_source_number"),
        CheckConstraint(
            "action IN ('pending','create','update','skip','warning','block')",
            name="ck_import_row_action",
        ),
        Index("ix_import_rows_batch_action", "batch_id", "action", "source_row_number"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("v3_import_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    sheet_key: Mapped[str] = mapped_column(String(20), nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_values: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False, default=dict)
    normalized_values: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, nullable=False, default=dict
    )
    product_code_normalized: Mapped[str | None] = mapped_column(String(100))
    port_id: Mapped[int | None] = mapped_column(Integer)
    target_product_id: Mapped[int | None] = mapped_column(Integer)
    target_period_id: Mapped[int | None] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False, default=dict)
    action: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON_VALUE, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ImportChange(Base):
    __tablename__ = "v3_import_changes"
    __table_args__ = (
        UniqueConstraint("batch_id", "sequence", name="uq_import_change_sequence"),
        CheckConstraint(
            "entity_type IN ('product','extension','price_period')",
            name="ck_import_change_entity_type",
        ),
        CheckConstraint(
            "action IN ('create','update','archive')", name="ck_import_change_action"
        ),
        CheckConstraint("sequence >= 1", name="ck_import_change_sequence"),
        Index("ix_import_changes_batch_entity", "batch_id", "entity_type", "entity_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("v3_import_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON_VALUE)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON_VALUE)
    expected_after_revision: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

