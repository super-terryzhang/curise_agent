"""Settings ORM models — admin-configurable rows.

Tables shared with v2 production. All five preserve their `v2_*` table names.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.db.base import Base


class FieldSchema(Base):
    __tablename__ = "v2_field_schemas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    definitions: Mapped[list[FieldDefinition]] = relationship(
        back_populates="schema",
        cascade="all, delete-orphan",
        order_by="FieldDefinition.sort_order",
    )


class FieldDefinition(Base):
    __tablename__ = "v2_field_definitions"
    __table_args__ = (UniqueConstraint("schema_id", "field_key", name="uq_field_def_schema_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    schema_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("v2_field_schemas.id", ondelete="CASCADE"),
        nullable=False,
    )
    field_key: Mapped[str] = mapped_column(String(50), nullable=False)
    field_label: Mapped[str] = mapped_column(String(100), nullable=False)
    field_type: Mapped[str] = mapped_column(String(20), default="string")
    is_core: Mapped[bool] = mapped_column(Boolean, default=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)
    extraction_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    schema: Mapped[FieldSchema] = relationship(back_populates="definitions")


class OrderFormatTemplate(Base):
    __tablename__ = "v2_order_format_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), default="excel")
    format_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    header_row: Mapped[int] = mapped_column(Integer, default=1)
    data_start_row: Mapped[int] = mapped_column(Integer, default=2)
    column_mapping: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    field_schema_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("v2_field_schemas.id"), nullable=True
    )
    sample_file_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    layout_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_fields: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    source_company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    match_keywords: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class DeliveryLocation(Base):
    __tablename__ = "v2_delivery_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    port_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("ports.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_person: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    delivery_notes: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ship_name_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class CompanyConfig(Base):
    __tablename__ = "v2_company_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
