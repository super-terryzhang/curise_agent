"""Atomic writes for extension values attached to Actor-visible core rows."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .errors import Conflict, ValidationError
from .history import append_change
from .models import DataRecord, DataTable, utc_now
from .permissions import require_writer
from .records import _persist
from .repository import creation_change, lock_tables, require_table, table_fields, transaction
from .schemas import Actor, FieldResponse, RecordResponse, SystemRecordUpdate
from .structures import check_version
from .system_tables import get_system_record, require_source_row
from .validation import normalize_record


def _extension_fields(db: Session, table_id: UUID) -> list[FieldResponse]:
    fields = [FieldResponse.model_validate(field) for field in table_fields(db, table_id)]
    if any(field.field_type == "link" for field in fields):
        raise ValidationError(
            "SYSTEM_LINK_FORBIDDEN", "系统数据表的扩展字段暂不支持关联记录"
        )
    if not any(field.status == "active" for field in fields):
        raise ValidationError("NO_ACTIVE_FIELDS", "请先配置启用的扩展字段，再保存扩展信息")
    return fields


def _request(body: SystemRecordUpdate, normalized: dict) -> dict:
    return {
        "request_id": str(body.request_id),
        "source_record_id": body.source_record_id,
        "schema_version": body.schema_version,
        "values": {key: normalized[key] for key in body.values},
    }


def _require_system_table(db: Session, table_id: UUID) -> DataTable:
    table = require_table(db, table_id)
    if table.table_kind != "system":
        raise ValidationError("NOT_SYSTEM_TABLE", "此保存方式只适用于系统数据表")
    return table


def _anchor_for_update(db: Session, table_id: UUID, source_record_id: str) -> DataRecord | None:
    return db.scalar(
        select(DataRecord)
        .where(
            DataRecord.table_id == table_id,
            DataRecord.source_record_id == source_record_id,
        )
        .with_for_update()
    )


def _retry(
    db: Session,
    table: DataTable,
    anchor: DataRecord,
    body: SystemRecordUpdate,
    actor: Actor,
) -> RecordResponse:
    original = creation_change(db, "record", anchor.id)
    if original is None:
        raise Conflict("CREATE_ID_CONFLICT", "此编号或来源记录已用于不同的创建请求")
    fields = [
        FieldResponse.model_validate(field)
        for field in original.display_snapshot["fields"].values()
    ]
    try:
        normalized = normalize_record(fields, body.values, create=True)
    except ValidationError as exc:
        raise Conflict("CREATE_ID_CONFLICT", "此编号已用于不同的创建内容") from exc
    if (
        anchor.id != body.request_id
        or anchor.table_id != table.id
        or anchor.source_record_id != body.source_record_id
        or original.actor_id != actor.id
        or original.actor_role != actor.role
        or original.creation_request != _request(body, normalized)
    ):
        raise Conflict("CREATE_ID_CONFLICT", "此编号或来源记录已用于不同的创建请求")
    return get_system_record(db, table, body.source_record_id, actor=actor)


def save_system_record(
    db: Session,
    table_id: UUID,
    body: SystemRecordUpdate,
    *,
    actor: Actor,
) -> RecordResponse:
    """Create or update one extension anchor without writing core business columns."""
    require_writer(actor)
    with transaction(db):
        lock_tables(db, [table_id], exclusive_ids=set())
        table = _require_system_table(db, table_id)
        require_source_row(
            db,
            table,
            body.source_record_id,
            actor=actor,
            for_update=True,
        )
        anchor = _anchor_for_update(db, table_id, body.source_record_id)

        if anchor is not None and body.expected_revision == 0:
            return _retry(db, table, anchor, body, actor)
        check_version(table, body.schema_version)
        fields = _extension_fields(db, table_id)
        if anchor is None:
            if body.expected_revision != 0:
                raise Conflict("RECORD_CHANGED", "扩展信息已变化，请刷新后核对")
            if db.get(DataRecord, body.request_id) is not None:
                raise Conflict("CREATE_ID_CONFLICT", "此编号已用于其他创建请求")
            normalized = normalize_record(fields, body.values, create=True)
            anchor = DataRecord(
                id=body.request_id,
                table_id=table_id,
                source_record_id=body.source_record_id,
                values={},
                schema_version=table.schema_version,
                created_by=actor.id,
                updated_by=actor.id,
            )
            db.add(anchor)
            _persist(db, anchor, fields, normalized)
            result = get_system_record(db, table, body.source_record_id, actor=actor)
            append_change(
                db,
                table_id=table_id,
                entity_type="record",
                entity_id=anchor.id,
                action="create_record",
                before=None,
                after=result.model_dump(mode="json"),
                actor=actor,
                schema_version=table.schema_version,
                revision=1,
                creation_request=_request(body, normalized),
            )
            return result

        if anchor.revision != body.expected_revision:
            raise Conflict("RECORD_CHANGED", "扩展信息已被他人修改，请刷新后核对")
        if anchor.status != "active":
            raise ValidationError("RECORD_ARCHIVED", "系统记录扩展信息不可归档")
        original = dict(anchor.values)
        normalized = normalize_record(fields, body.values, existing=original, create=False)
        before = get_system_record(db, table, body.source_record_id, actor=actor).model_dump(
            mode="json"
        )
        anchor.revision += 1
        anchor.schema_version = table.schema_version
        anchor.updated_at, anchor.updated_by = utc_now(), actor.id
        _persist(db, anchor, fields, normalized)
        result = get_system_record(db, table, body.source_record_id, actor=actor)
        append_change(
            db,
            table_id=table_id,
            entity_type="record",
            entity_id=anchor.id,
            action="update_record",
            before=before,
            after=result.model_dump(mode="json"),
            actor=actor,
            schema_version=table.schema_version,
            revision=anchor.revision,
        )
        return result
