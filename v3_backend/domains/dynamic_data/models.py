"""Fixed storage for logical tables; relationships use table-scoped identities."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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


class Identity:
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DataTable(Identity, Timestamps, Base):
    __tablename__ = "v3_data_tables"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="ck_data_table_status"),
        CheckConstraint("table_kind IN ('user', 'system')", name="ck_data_table_kind"),
        CheckConstraint(
            "(table_kind = 'system' AND system_key IS NOT NULL) OR "
            "(table_kind = 'user' AND system_key IS NULL)",
            name="ck_data_table_system_key",
        ),
        CheckConstraint("schema_version >= 1", name="ck_data_table_version"),
        UniqueConstraint("system_key", name="uq_data_table_system_key"),
        ForeignKeyConstraint(
            ["id", "display_field_id"],
            ["v3_data_fields.table_id", "v3_data_fields.id"],
            name="fk_data_table_display_field",
            use_alter=True,
            ondelete="RESTRICT",
        ),
    )
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    table_kind: Mapped[str] = mapped_column(String(10), default="user")
    system_key: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(10), default="active")
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    display_field_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    created_by: Mapped[int] = mapped_column(Integer)
    updated_by: Mapped[int] = mapped_column(Integer)


class DataField(Identity, Timestamps, Base):
    __tablename__ = "v3_data_fields"
    __table_args__ = (
        UniqueConstraint("table_id", "id", name="uq_data_field_table_id"),
        UniqueConstraint("table_id", "id", "target_table_id", name="uq_data_field_target"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_data_field_status"),
        CheckConstraint("schema_version >= 1", name="ck_data_field_version"),
        CheckConstraint("sort_order >= 0", name="ck_data_field_order"),
        CheckConstraint(
            "field_type IN ('text','number','date','datetime','single_select',"
            "'multi_select','boolean','link')",
            name="ck_data_field_type",
        ),
        CheckConstraint(
            "(field_type = 'link' AND target_table_id IS NOT NULL) OR "
            "(field_type <> 'link' AND target_table_id IS NULL)",
            name="ck_data_field_link",
        ),
        CheckConstraint(
            "NOT \"unique\" OR field_type IN ('text','number')", name="ck_data_field_unique_type"
        ),
        Index("ix_data_fields_table_status", "table_id", "status", "sort_order"),
    )
    table_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("v3_data_tables.id", ondelete="RESTRICT")
    )
    label: Mapped[str] = mapped_column(String(100))
    field_type: Mapped[str] = mapped_column(String(20))
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    unique: Mapped[bool] = mapped_column(Boolean, default=False)
    default_value: Mapped[Any | None] = mapped_column(JSON_VALUE, nullable=True)
    config: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    target_table_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("v3_data_tables.id", ondelete="RESTRICT")
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(10), default="active")
    schema_version: Mapped[int] = mapped_column(Integer, default=1)

    @property
    def source(self) -> str:
        return "extension"

    @property
    def locked(self) -> bool:
        return False

    @property
    def system_key(self) -> None:
        return None


class DataRecord(Identity, Timestamps, Base):
    __tablename__ = "v3_data_records"
    __table_args__ = (
        UniqueConstraint("table_id", "id", name="uq_data_record_table_id"),
        UniqueConstraint("table_id", "source_record_id", name="uq_data_record_source_record_id"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_data_record_status"),
        CheckConstraint(
            "source_record_id IS NULL OR source_record_id <> ''",
            name="ck_data_record_source_record_id",
        ),
        CheckConstraint("revision >= 1 AND schema_version >= 1", name="ck_data_record_versions"),
        Index("ix_data_records_table_status", "table_id", "status", "created_at"),
    )
    table_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("v3_data_tables.id", ondelete="RESTRICT")
    )
    source_record_id: Mapped[str | None] = mapped_column(String(100))
    values: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(10), default="active")
    created_by: Mapped[int] = mapped_column(Integer)
    updated_by: Mapped[int] = mapped_column(Integer)


class DataLink(Base):
    __tablename__ = "v3_data_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["table_id", "record_id"],
            ["v3_data_records.table_id", "v3_data_records.id"],
            name="fk_data_link_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["table_id", "field_id", "target_table_id"],
            ["v3_data_fields.table_id", "v3_data_fields.id", "v3_data_fields.target_table_id"],
            name="fk_data_link_field_target",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["target_table_id", "target_record_id"],
            ["v3_data_records.table_id", "v3_data_records.id"],
            name="fk_data_link_target",
            ondelete="RESTRICT",
        ),
        Index("ix_data_links_target", "target_table_id", "target_record_id"),
    )
    table_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    record_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    field_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    target_table_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    target_record_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class DataUniqueValue(Base):
    __tablename__ = "v3_data_unique_values"
    __table_args__ = (
        UniqueConstraint("field_id", "value", name="uq_data_unique_value"),
        ForeignKeyConstraint(
            ["table_id", "record_id"],
            ["v3_data_records.table_id", "v3_data_records.id"],
            name="fk_data_unique_record",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["table_id", "field_id"],
            ["v3_data_fields.table_id", "v3_data_fields.id"],
            name="fk_data_unique_field",
            ondelete="RESTRICT",
        ),
    )
    table_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    record_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    field_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))


class DataChange(Identity, Base):
    __tablename__ = "v3_data_changes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["table_id", "field_id"],
            ["v3_data_fields.table_id", "v3_data_fields.id"],
            name="fk_data_change_field",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["table_id", "record_id"],
            ["v3_data_records.table_id", "v3_data_records.id"],
            name="fk_data_change_record",
            ondelete="RESTRICT",
        ),
        CheckConstraint("entity_type IN ('table','field','record')", name="ck_data_change_entity"),
        CheckConstraint("schema_version >= 1", name="ck_data_change_version"),
        Index("ix_data_changes_history", "table_id", "created_at", "id"),
    )
    table_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("v3_data_tables.id", ondelete="RESTRICT")
    )
    field_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    record_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    entity_type: Mapped[str] = mapped_column(String(10))
    entity_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    action: Mapped[str] = mapped_column(String(30))
    before: Mapped[dict | None] = mapped_column(JSON_VALUE)
    after: Mapped[dict | None] = mapped_column(JSON_VALUE)
    display_snapshot: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    creation_request: Mapped[dict | None] = mapped_column(JSON_VALUE)
    actor_id: Mapped[int] = mapped_column(Integer)
    actor_role: Mapped[str] = mapped_column(String(30))
    schema_version: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
