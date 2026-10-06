"""Public contracts: unknown keys and system metadata are never writable."""

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FieldType = Literal[
    "text", "number", "date", "datetime", "single_select", "multi_select", "boolean", "link"
]
Status = Literal["active", "archived"]
TableKind = Literal["user", "system"]
SystemTableKey = Literal["products", "suppliers", "orders"]
FieldSource = Literal["core", "extension"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class Actor(Contract):
    id: int
    role: str


class SchemaAction(Contract):
    expected_schema_version: int = Field(ge=1)


class RecordAction(Contract):
    expected_revision: int = Field(ge=1)
    schema_version: int = Field(ge=1)


class Labelled(Contract):
    @field_validator("name", "label", check_fields=False)
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("名称不能为空")
        return value


class TableCreate(Labelled):
    id: UUID
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)


class TableUpdate(SchemaAction, Labelled):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    display_field_id: UUID | None = None


class TextConfig(Contract):
    max_length: int = Field(default=4096, ge=1, le=4096)
    multiline: bool = False


class NumberConfig(Contract):
    precision: int = Field(default=18, ge=1, le=18)
    scale: int = Field(default=6, ge=0, le=6)

    @model_validator(mode="after")
    def scale_fits(self):
        if self.scale > self.precision:
            raise ValueError("小数位数不能超过总位数")
        return self


class SelectOption(Labelled):
    id: UUID
    label: str = Field(min_length=1, max_length=100)
    active: bool = True


class SelectConfig(Contract):
    options: list[SelectOption] = Field(default_factory=list, max_length=100)


class FieldDefinition(Labelled):
    id: UUID
    label: str = Field(min_length=1, max_length=100)
    field_type: FieldType
    required: bool = False
    unique: bool = False
    default_value: Any = None
    config: dict[str, Any] = Field(default_factory=dict)
    target_table_id: UUID | None = None


class FieldCreate(FieldDefinition, SchemaAction):
    pass


class FieldUpdate(SchemaAction, Labelled):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    field_type: FieldType | None = None
    required: bool | None = None
    unique: bool | None = None
    default_value: Any = None
    config: dict[str, Any] | None = None
    target_table_id: UUID | None = None


class FieldReorder(SchemaAction):
    field_ids: list[UUID] = Field(max_length=100)


class RecordCreate(Contract):
    id: UUID
    schema_version: int = Field(ge=1)
    values: dict[str, Any]


class RecordUpdate(RecordAction):
    values: dict[str, Any]


class SystemRecordUpdate(Contract):
    request_id: UUID
    source_record_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0)
    schema_version: int = Field(ge=1)
    values: dict[str, Any]


class TableResponse(TableCreate):
    table_kind: TableKind
    system_key: SystemTableKey | None
    status: Status
    schema_version: int
    display_field_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    created_by: int
    updated_by: int
    field_count: int = 0
    record_count: int = 0


class FieldResponse(FieldDefinition):
    table_id: UUID
    source: FieldSource
    locked: bool
    system_key: str | None
    status: Status
    sort_order: int
    schema_version: int
    created_at: datetime
    updated_at: datetime


class RecordResponse(Contract):
    id: UUID
    table_id: UUID
    values: dict[str, Any]
    revision: int
    schema_version: int
    status: Status
    created_at: datetime
    updated_at: datetime
    created_by: int
    updated_by: int
    display_label: str
    linked_labels: dict[str, dict[str, str]] = Field(default_factory=dict)


class ChangeResponse(Contract):
    id: UUID
    table_id: UUID
    entity_type: str
    entity_id: UUID
    field_id: UUID | None
    record_id: UUID | None
    action: str
    before: dict | None
    after: dict | None
    display_snapshot: dict
    actor_id: int
    actor_role: str
    schema_version: int
    revision: int | None
    created_at: datetime


T = TypeVar("T")


class Page(Contract, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class ErrorIssue(Contract):
    table_id: UUID | None = None
    record_id: UUID | None = None
    field_id: UUID | None = None
    field_label: str | None = None
    code: str
    message: str


class RecordFilter(Contract):
    field_id: UUID
    operator: Literal["eq", "contains", "lt", "lte", "gt", "gte", "is_empty"]
    value: Any = None


class RecordQuery(Contract):
    status: Status = "active"
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)
    sort_field_id: UUID | None = None
    sort_direction: Literal["asc", "desc"] = "asc"
    filters: list[RecordFilter] = Field(default_factory=list, max_length=10)


class ChangeQuery(Contract):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)
    entity_type: Literal["table", "field", "record"] | None = None
    record_id: UUID | None = None
