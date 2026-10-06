"""Atomic record writes with stable retry identity and locked link targets."""

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .errors import Conflict, ValidationError
from .history import append_change
from .models import DataLink, DataRecord, DataUniqueValue, utc_now
from .permissions import require_writer
from .repository import (
    creation_change,
    get_record_values,
    lock_records,
    lock_tables,
    require_record,
    require_table,
    table_fields,
    transaction,
)
from .schemas import Actor, ErrorIssue, FieldResponse, RecordCreate, RecordResponse, RecordUpdate
from .structures import check_version
from .validation import normalize_record, unique_value


def record_response(db: Session, record: DataRecord, values: dict | None = None) -> RecordResponse:
    actual = get_record_values(db, record) if values is None else values
    table = require_table(db, record.table_id, active=False)
    display = actual.get(str(table.display_field_id)) if table.display_field_id else None
    label = display if isinstance(display, str) and display.strip() else f"记录 {record.id}"
    return RecordResponse(
        id=record.id,
        table_id=record.table_id,
        values=actual,
        revision=record.revision,
        schema_version=record.schema_version,
        status=record.status,
        created_at=record.created_at,
        updated_at=record.updated_at,
        created_by=record.created_by,
        updated_by=record.updated_by,
        display_label=label,
    )


def _request(body: RecordCreate, normalized: dict) -> dict:
    return {
        "id": str(body.id),
        "schema_version": body.schema_version,
        "values": {key: normalized[key] for key in body.values},
    }


def _retry(db: Session, table_id: UUID, body: RecordCreate, actor: Actor):
    row = db.get(DataRecord, body.id)
    if row is None:
        return None
    original = creation_change(db, "record", body.id)
    if (
        row.table_id != table_id
        or original is None
        or original.table_id != table_id
        or original.actor_id != actor.id
        or original.actor_role != actor.role
    ):
        raise Conflict("CREATE_ID_CONFLICT", "此编号已用于其他创建请求，请核对已有记录")
    fields = [FieldResponse.model_validate(f) for f in original.display_snapshot["fields"].values()]
    try:
        normalized = normalize_record(fields, body.values, create=True)
    except ValidationError as exc:
        raise Conflict("CREATE_ID_CONFLICT", "此编号已用于不同的创建内容") from exc
    if original.creation_request != _request(body, normalized):
        raise Conflict("CREATE_ID_CONFLICT", "此编号已用于不同的创建内容，请核对已有记录")
    return record_response(db, row)


def _link_ids(fields, *value_sets):
    return {
        UUID(values[str(f.id)])
        for values in value_sets
        for f in fields
        if f.status == "active" and f.field_type == "link" and values.get(str(f.id)) is not None
    }


def _check_links(db, fields, values, targets):
    for f in fields:
        value = values.get(str(f.id))
        if f.status != "active" or f.field_type != "link" or value is None:
            continue
        target = targets.get(UUID(value))
        if target is None or target.table_id != f.target_table_id or target.status != "active":
            raise ValidationError(
                "INVALID_LINK_TARGET",
                "关联记录不存在、已归档或属于其他表",
                [
                    ErrorIssue(
                        table_id=f.table_id,
                        field_id=f.id,
                        field_label=f.label,
                        code="INVALID_LINK_TARGET",
                        message="请选择指定目标表的启用记录",
                    )
                ],
            )
        require_table(db, f.target_table_id)


def _prepare(db, table_id, schema_version, values, original_record=None):
    fields = [FieldResponse.model_validate(f) for f in table_fields(db, table_id)]
    if not any(f.status == "active" for f in fields):
        raise ValidationError("NO_ACTIVE_FIELDS", "请先配置启用字段，再新增或保存记录")
    original = get_record_values(db, original_record) if original_record else {}
    normalized = normalize_record(fields, values, existing=original, create=original_record is None)
    table_ids = {table_id} | {
        f.target_table_id for f in fields if f.status == "active" and f.target_table_id
    }
    link_ids = _link_ids(fields, original, normalized)
    # No write has occurred. Determine the complete lock set before taking any
    # locks; after the source lock, schema changes must reject rather than add locks.
    lock_tables(db, list(table_ids), exclusive_ids=set())
    table = require_table(db, table_id)
    check_version(table, schema_version)
    refreshed = [FieldResponse.model_validate(f) for f in table_fields(db, table_id)]
    refreshed_tables = {table_id} | {
        f.target_table_id for f in refreshed if f.status == "active" and f.target_table_id
    }
    if refreshed_tables != table_ids:
        raise Conflict("SCHEMA_CHANGED", "关联结构已变化，请刷新后核对")
    for target_id in link_ids:
        if db.get(DataRecord, target_id) is None:
            _check_links(db, refreshed, normalized, {})
            raise ValidationError("INVALID_LINK_TARGET", "旧关联记录不存在，请刷新后核对")
    record_ids = link_ids | ({original_record.id} if original_record else set())
    locked = {
        r.id: r
        for r in lock_records(
            db, list(record_ids), write_ids={original_record.id} if original_record else set()
        )
    }
    if original_record:
        current = require_record(db, table_id, original_record.id)
        original = get_record_values(db, current)
        normalized = normalize_record(refreshed, values, existing=original, create=False)
    else:
        current = None
        normalized = normalize_record(refreshed, values, create=True)
    if not _link_ids(refreshed, original, normalized).issubset(link_ids):
        raise Conflict("RECORD_CHANGED", "关联记录已被修改，请刷新后核对")
    _check_links(db, refreshed, normalized, locked)
    return table, refreshed, current, original, normalized


def _persist(db, record, fields, values):
    link_keys = {str(f.id) for f in fields if f.field_type == "link"}
    record.values = {key: value for key, value in values.items() if key not in link_keys}
    db.flush()
    for f in fields:
        if f.status != "active":
            continue
        value = values.get(str(f.id))
        if f.field_type == "link":
            db.execute(
                delete(DataLink).where(DataLink.record_id == record.id, DataLink.field_id == f.id)
            )
            if value is not None:
                db.add(
                    DataLink(
                        table_id=record.table_id,
                        record_id=record.id,
                        field_id=f.id,
                        target_table_id=f.target_table_id,
                        target_record_id=UUID(value),
                    )
                )
        if f.unique:
            canonical = unique_value(f, value)
            other = (
                db.scalar(
                    select(DataUniqueValue).where(
                        DataUniqueValue.field_id == f.id,
                        DataUniqueValue.value == canonical,
                        DataUniqueValue.record_id != record.id,
                    )
                )
                if canonical is not None
                else None
            )
            if other:
                raise Conflict(
                    "DUPLICATE_VALUE",
                    "字段值与已有记录重复（含归档记录）",
                    [
                        ErrorIssue(
                            table_id=record.table_id,
                            record_id=record.id,
                            field_id=f.id,
                            field_label=f.label,
                            code="DUPLICATE_VALUE",
                            message="此值已被另一条记录使用",
                        )
                    ],
                )
            db.execute(
                delete(DataUniqueValue).where(
                    DataUniqueValue.record_id == record.id, DataUniqueValue.field_id == f.id
                )
            )
            if canonical is not None:
                db.add(
                    DataUniqueValue(
                        table_id=record.table_id,
                        record_id=record.id,
                        field_id=f.id,
                        value=canonical,
                    )
                )
    db.flush()


def create_record(
    db: Session, table_id: UUID, body: RecordCreate, *, actor: Actor
) -> RecordResponse:
    require_writer(actor)
    try:
        with transaction(db):
            retried = _retry(db, table_id, body, actor)
            if retried:
                return retried
            table, fields, _, _, normalized = _prepare(
                db, table_id, body.schema_version, body.values
            )
            record = DataRecord(
                id=body.id,
                table_id=table_id,
                values={},
                schema_version=table.schema_version,
                created_by=actor.id,
                updated_by=actor.id,
            )
            db.add(record)
            _persist(db, record, fields, normalized)
            result = record_response(db, record, normalized)
            append_change(
                db,
                table_id=table_id,
                entity_type="record",
                entity_id=record.id,
                action="create_record",
                before=None,
                after=result.model_dump(mode="json"),
                actor=actor,
                schema_version=table.schema_version,
                revision=1,
                creation_request=_request(body, normalized),
            )
        return result
    except Conflict:
        retried = _retry(db, table_id, body, actor)
        if retried is None:
            raise
        return retried


def update_record(
    db: Session, table_id: UUID, record_id: UUID, body: RecordUpdate, *, actor: Actor
) -> RecordResponse:
    require_writer(actor)
    with transaction(db):
        initial = require_record(db, table_id, record_id)
        table, fields, record, original, normalized = _prepare(
            db, table_id, body.schema_version, body.values, initial
        )
        if record.revision != body.expected_revision:
            raise Conflict("RECORD_CHANGED", "记录已被他人修改，请刷新后核对，不会覆盖当前数据")
        if record.status != "active":
            raise ValidationError("RECORD_ARCHIVED", "记录已归档，请先恢复")
        before = record_response(db, record, original).model_dump(mode="json")
        record.revision += 1
        record.schema_version = table.schema_version
        record.updated_at, record.updated_by = utc_now(), actor.id
        _persist(db, record, fields, normalized)
        result = record_response(db, record, normalized)
        append_change(
            db,
            table_id=table_id,
            entity_type="record",
            entity_id=record_id,
            action="update_record",
            before=before,
            after=result.model_dump(mode="json"),
            actor=actor,
            schema_version=table.schema_version,
            revision=record.revision,
        )
    return result
