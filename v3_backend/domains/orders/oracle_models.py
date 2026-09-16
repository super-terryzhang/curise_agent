"""Source identity for the single-PO import (revision adoption is a later phase)."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.db.base import Base


class OraclePOImport(Base):
    __tablename__ = "v3_oracle_po_imports"

    source_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_key: Mapped[str] = mapped_column(String(64), nullable=False)
    po_number: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source_record: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    pdf_sha256: Mapped[str | None] = mapped_column(String(64))
    document_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("v2_documents.id"), unique=True
    )
    order_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("v2_orders.id"), unique=True)
    inquiry_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("v3_inquiries.id"))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="processing")
    stage: Mapped[str] = mapped_column(String(30), nullable=False, default="download")
    issues: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class OracleScanRun(Base):
    __tablename__ = "v3_oracle_scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Only one queued/running scan across all API instances and job executions.
    active_key: Mapped[str | None] = mapped_column(String(20), unique=True)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
