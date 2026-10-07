"""Archive rather than delete; restore checks the constraints being re-enabled."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .errors import Conflict, ValidationError
from .history import append_change
from .models import DataField, DataLink, DataRecord, DataTable, utc_now
from .permissions import require_admin, require_writer
from .records import _check_links, _prepare, record_response
from .repository import (
    get_record_values,
    lock_records,
    lock_tables,
    require_field,
    require_record,
    require_table,
    table_fields,
    transaction,
)
from .schemas import (
    Actor,
    ErrorIssue,
    FieldResponse,
    RecordAction,
    RecordResponse,
    SchemaAction,
    TableResponse,
)
from .structures import bump, check_version, ensure_unique_active_field_label
from .validation import normalize_record


def _inbound(db, table_id, record_id=None):
    query = (
        select(DataLink)
        .join(DataRecord, DataRecord.id == DataLink.record_id)
        .join(DataField, DataField.id == DataLink.field_id)
        .join(DataTable, DataTable.id == DataLink.table_id)
        .where(
            DataLink.target_table_id == table_id,
            DataRecord.status == "active",
            DataField.status == "active",
            DataTable.status == "active",
        )
    )
    if record_id:
        query = query.where(DataLink.target_record_id == record_id, DataLink.record_id != record_id)
    else:
        query = query.where(DataLink.table_id != table_id)
    return list(db.scalars(query))


def _reject_inbound(links):
    if links:
        raise ValidationError(
            "ACTIVE_REFERENCES",
            "仍有启用记录引用此数据，请先解除关联或归档来源",
            [
                ErrorIssue(
                    table_id=link.table_id,
                    record_id=link.record_id,
                    field_id=link.field_id,
                    code="ACTIVE_REFERENCE",
                    message="此记录仍在使用目标关联",
                )
                for link in links
            ],
        )


def _restore_values(db, table_id, fields, records):
    active = [f for f in fields if f.status == "active"]
    values = {r.id: get_record_values(db, r) for r in records}
    linked_ids = {
        UUID(v[str(f.id)])
        for v in values.values()
        for f in active
        if f.field_type == "link" and v.get(str(f.id))
    }
    targets = {r.id: r for r in lock_records(db, list(linked_ids), write_ids=set())}
    for record in records:
        try:
            normalized = normalize_record(fields, {}, existing=values[record.id], create=False)
            _check_links(db, fields, normalized, targets)
        except ValidationError as exc:
            for issue in exc.issues:
                issue.record_id = record.id
                issue.table_id = table_id
            raise


def _structure_locks(db, table_id, expected, *, restoring_field_id=None):
    initial = table_fields(db, table_id)
    targets = {
        f.target_table_id
        for f in initial
        if f.target_table_id and (f.status == "active" or f.id == restoring_field_id)
    }
    lock_tables(db, [table_id, *targets], exclusive_ids={table_id})
    table = require_table(db, table_id, active=False)
    check_version(table, expected)
    fields = [FieldResponse.model_validate(f) for f in table_fields(db, table_id)]
    fresh = {
        f.target_table_id
        for f in fields
        if f.target_table_id and (f.status == "active" or f.id == restoring_field_id)
    }
    if fresh != targets:
        raise Conflict("SCHEMA_CHANGED", "关联配置已变化，请刷新后核对")
    return table, fields


def set_table_status(
    db: Session, table_id: UUID, body: SchemaAction, *, active: bool, actor: Actor
) -> TableResponse:
    require_admin(actor)
    with transaction(db):
        table, fields = _structure_locks(db, table_id, body.expected_schema_version)
        if table.table_kind == "system":
            raise ValidationError("SYSTEM_TABLE_LOCKED", "系统数据表不可归档或恢复")
        desired = "active" if active else "archived"
        if table.status == desired:
            return TableResponse.model_validate(table)
        before = TableResponse.model_validate(table).model_dump(mode="json")
        if not active:
            inbound_fields = db.scalars(
                select(DataField)
                .join(DataTable, DataTable.id == DataField.table_id)
                .where(
                    DataField.target_table_id == table_id,
                    DataField.table_id != table_id,
                    DataField.status == "active",
                    DataTable.status == "active",
                )
            ).all()
            if inbound_fields:
                raise ValidationError(
                    "ACTIVE_LINK_FIELDS",
                    "启用的关联字段仍指向此表，请先归档这些字段或来源表",
                    [
                        ErrorIssue(
                            table_id=f.table_id,
                            field_id=f.id,
                            field_label=f.label,
                            code="ACTIVE_LINK_FIELD",
                            message="此关联字段仍使用目标表",
                        )
                        for f in inbound_fields
                    ],
                )
            _reject_inbound(_inbound(db, table_id))
        table.status = desired
        if active:
            # Make self-table references active in this transaction; failures
            # roll the status back together with all other changes.
            db.flush()
            for f in fields:
                if f.status == "active" and f.target_table_id:
                    require_table(db, f.target_table_id)
            records = db.scalars(
                select(DataRecord).where(
                    DataRecord.table_id == table_id, DataRecord.status == "active"
                )
            ).all()
            _restore_values(db, table_id, fields, records)
        bump(table, actor)
        db.flush()
        result = TableResponse.model_validate(table)
        append_change(
            db,
            table_id=table_id,
            entity_type="table",
            entity_id=table_id,
            action="restore_table" if active else "archive_table",
            before=before,
            after=result.model_dump(mode="json"),
            actor=actor,
            schema_version=table.schema_version,
        )
    return result


def set_field_status(
    db: Session, table_id: UUID, field_id: UUID, body: SchemaAction, *, active: bool, actor: Actor
) -> FieldResponse:
    require_admin(actor)
    with transaction(db):
        table, _ = _structure_locks(
            db,
            table_id,
            body.expected_schema_version,
            restoring_field_id=field_id if active else None,
        )
        require_table(db, table_id)
        field = require_field(db, table_id, field_id)
        desired = "active" if active else "archived"
        if field.status == desired:
            return FieldResponse.model_validate(field)
        before = FieldResponse.model_validate(field).model_dump(mode="json")
        if active:
            ensure_unique_active_field_label(
                db, table_id, field.label, exclude_field_id=field_id
            )
        field.status = desired
        if not active and table.display_field_id == field_id:
            table.display_field_id = None
        if active:
            if field.target_table_id:
                require_table(db, field.target_table_id)
            db.flush()
            fields = [FieldResponse.model_validate(f) for f in table_fields(db, table_id)]
            rows = db.scalars(
                select(DataRecord).where(
                    DataRecord.table_id == table_id, DataRecord.status == "active"
                )
            ).all()
            _restore_values(db, table_id, fields, rows)
        bump(table, actor)
        field.schema_version, field.updated_at = table.schema_version, utc_now()
        db.flush()
        result = FieldResponse.model_validate(field)
        append_change(
            db,
            table_id=table_id,
            entity_type="field",
            entity_id=field_id,
            action="restore_field" if active else "archive_field",
            before=before,
            after=result.model_dump(mode="json"),
            actor=actor,
            schema_version=table.schema_version,
        )
    return result


def set_record_status(
    db: Session, table_id: UUID, record_id: UUID, body: RecordAction, *, active: bool, actor: Actor
) -> RecordResponse:
    require_writer(actor)
    with transaction(db):
        if require_table(db, table_id, active=False).table_kind == "system":
            raise ValidationError("SYSTEM_RECORD_LOCKED", "系统记录不可在数据表页面归档")
        initial = require_record(db, table_id, record_id)
        if active:
            table, _, record, _, _ = _prepare(
                db, table_id, body.schema_version, {}, initial, restoring_record_id=record_id
            )
        else:
            lock_tables(db, [table_id], exclusive_ids=set())
            table = require_table(db, table_id)
            check_version(table, body.schema_version)
            lock_records(db, [record_id], write_ids={record_id})
            record = require_record(db, table_id, record_id)
        if record.revision != body.expected_revision:
            raise Conflict("RECORD_CHANGED", "记录已被修改，请刷新后核对")
        desired = "active" if active else "archived"
        if record.status == desired:
            return record_response(db, record)
        before = record_response(db, record).model_dump(mode="json")
        if not active:
            _reject_inbound(_inbound(db, table_id, record_id))
        record.status, record.revision = desired, record.revision + 1
        record.schema_version = table.schema_version
        record.updated_at, record.updated_by = utc_now(), actor.id
        db.flush()
        result = record_response(db, record)
        append_change(
            db,
            table_id=table_id,
            entity_type="record",
            entity_id=record_id,
            action="restore_record" if active else "archive_record",
            before=before,
            after=result.model_dump(mode="json"),
            actor=actor,
            schema_version=table.schema_version,
            revision=record.revision,
        )
    return result
