"""Structure changes are versioned, serialized, and validated against old data."""

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .errors import Conflict, ValidationError
from .history import append_change
from .models import DataField, DataLink, DataRecord, DataTable, DataUniqueValue, utc_now
from .permissions import require_admin
from .repository import (
    creation_change,
    lock_tables,
    require_field,
    require_table,
    table_fields,
    transaction,
)
from .schemas import (
    Actor,
    ErrorIssue,
    FieldCreate,
    FieldDefinition,
    FieldReorder,
    FieldResponse,
    FieldUpdate,
    TableCreate,
    TableResponse,
    TableUpdate,
)
from .validation import MAX_FIELDS, normalize_value, unique_value, validate_field_definition


def check_version(table: DataTable, expected: int) -> None:
    if table.schema_version != expected:
        raise Conflict("SCHEMA_CHANGED", "字段结构已变化，请刷新后重新核对，不会覆盖当前数据")


def bump(table: DataTable, actor: Actor) -> None:
    table.schema_version += 1
    table.updated_at = utc_now()
    table.updated_by = actor.id


def _retry(db, model, entity_type, entity_id, table_id, request, actor):
    row = db.get(model, entity_id)
    if row is None:
        return None
    original = creation_change(db, entity_type, entity_id)
    if (
        original is None
        or original.table_id != table_id
        or original.actor_id != actor.id
        or original.actor_role != actor.role
        or original.creation_request != request
    ):
        raise Conflict("CREATE_ID_CONFLICT", "此编号已用于不同的创建请求，请核对已保存的数据")
    return row


def create_table(db: Session, body: TableCreate, *, actor: Actor) -> TableResponse:
    require_admin(actor)
    request = body.model_dump(mode="json")
    try:
        with transaction(db):
            existing = _retry(db, DataTable, "table", body.id, body.id, request, actor)
            if existing:
                return TableResponse.model_validate(existing)
            row = DataTable(**body.model_dump(), created_by=actor.id, updated_by=actor.id)
            db.add(row)
            db.flush()
            result = TableResponse.model_validate(row)
            append_change(
                db,
                table_id=row.id,
                entity_type="table",
                entity_id=row.id,
                action="create_table",
                before=None,
                after=result.model_dump(mode="json"),
                actor=actor,
                schema_version=1,
                creation_request=request,
            )
        return result
    except Conflict:
        existing = _retry(db, DataTable, "table", body.id, body.id, request, actor)
        if existing is None:
            raise
        return TableResponse.model_validate(existing)


def update_table(db: Session, table_id: UUID, body: TableUpdate, *, actor: Actor) -> TableResponse:
    require_admin(actor)
    with transaction(db):
        lock_tables(db, [table_id], exclusive_ids={table_id})
        table = require_table(db, table_id)
        if table.table_kind == "system":
            raise ValidationError("SYSTEM_TABLE_LOCKED", "系统数据表的名称和配置不可修改")
        check_version(table, body.expected_schema_version)
        changes = body.model_dump(exclude={"expected_schema_version"}, exclude_unset=True)
        if "name" in changes and changes["name"] is None:
            raise ValidationError("REQUIRED_TABLE_NAME", "数据表名称不能为空")
        display_id = changes.get("display_field_id", table.display_field_id)
        if display_id:
            display = require_field(db, table_id, display_id)
            if display.field_type != "text" or display.status != "active":
                raise ValidationError("INVALID_DISPLAY_FIELD", "显示名称必须选择此表的启用文本字段")
        before = TableResponse.model_validate(table).model_dump(mode="json")
        for key, value in changes.items():
            setattr(table, key, value)
        bump(table, actor)
        db.flush()
        result = TableResponse.model_validate(table)
        append_change(
            db,
            table_id=table_id,
            entity_type="table",
            entity_id=table_id,
            action="update_table",
            before=before,
            after=result.model_dump(mode="json"),
            actor=actor,
            schema_version=table.schema_version,
        )
    return result


def _lock_structure(db, table_id, expected, target_ids):
    lock_tables(db, [table_id, *target_ids], exclusive_ids={table_id})
    table = require_table(db, table_id)
    check_version(table, expected)
    for target_id in target_ids:
        require_table(db, target_id)
    return table


def _validate_existing(db, table_id, definition: FieldDefinition):
    key = str(definition.id)
    issues = []
    records = list(db.scalars(select(DataRecord).where(DataRecord.table_id == table_id)))
    links = (
        {
            link.record_id: str(link.target_record_id)
            for link in db.scalars(
                select(DataLink).where(
                    DataLink.table_id == table_id, DataLink.field_id == definition.id
                )
            )
        }
        if definition.field_type == "link"
        else {}
    )
    for row in records:
        if row.status != "active":
            continue
        value = links.get(row.id) if definition.field_type == "link" else row.values.get(key)
        old = value if isinstance(value, list) else [value]
        try:
            normalize_value(
                definition, value, allow_inactive_option_ids={v for v in old if isinstance(v, str)}
            )
        except ValidationError as exc:
            issues.extend(
                issue.model_copy(update={"table_id": table_id, "record_id": row.id})
                for issue in exc.issues
            )
    if issues:
        raise ValidationError(
            "EXISTING_RECORDS_INVALID", "新规则使已有记录不合法，请先修正列出的记录", issues
        )
    return records


def _reserve_unique(db, table_id, definition, records):
    reservations, seen, issues = [], {}, []
    for row in records:
        value = unique_value(definition, row.values.get(str(definition.id)))
        if value is None:
            continue
        if value in seen:
            for record_id in (seen[value], row.id):
                issues.append(
                    ErrorIssue(
                        table_id=table_id,
                        record_id=record_id,
                        field_id=definition.id,
                        field_label=definition.label,
                        code="DUPLICATE_VALUE",
                        message="此字段与其他记录重复（含归档记录）",
                    )
                )
        else:
            seen[value] = row.id
            reservations.append(
                DataUniqueValue(
                    table_id=table_id, record_id=row.id, field_id=definition.id, value=value
                )
            )
    if issues:
        raise Conflict("DUPLICATE_VALUE", "已有重复值，不能启用唯一约束", issues)
    db.add_all(reservations)


def create_field(db: Session, table_id: UUID, body: FieldCreate, *, actor: Actor) -> FieldResponse:
    require_admin(actor)
    definition = validate_field_definition(body)
    request = {
        **definition.model_dump(mode="json"),
        "expected_schema_version": body.expected_schema_version,
    }
    try:
        with transaction(db):
            existing = _retry(db, DataField, "field", body.id, table_id, request, actor)
            if existing:
                return FieldResponse.model_validate(existing)
            targets = [definition.target_table_id] if definition.target_table_id else []
            table = _lock_structure(db, table_id, body.expected_schema_version, targets)
            if table.table_kind == "system" and definition.field_type == "link":
                raise ValidationError(
                    "SYSTEM_LINK_FORBIDDEN", "系统数据表的扩展字段暂不支持关联记录"
                )
            for target_id in targets:
                if require_table(db, target_id).table_kind == "system":
                    raise ValidationError(
                        "SYSTEM_LINK_FORBIDDEN", "第一轮不支持关联到系统数据表"
                    )
            fields = table_fields(db, table_id)
            if len(fields) >= MAX_FIELDS:
                raise ValidationError("FIELD_LIMIT", "每张表最多 100 个字段（含归档字段）")
            records = _validate_existing(db, table_id, definition)
            bump(table, actor)
            row = DataField(
                **definition.model_dump(),
                table_id=table_id,
                sort_order=max((f.sort_order for f in fields), default=-1) + 1,
                schema_version=table.schema_version,
            )
            db.add(row)
            db.flush()
            if definition.unique:
                _reserve_unique(db, table_id, definition, records)
            result = FieldResponse.model_validate(row)
            append_change(
                db,
                table_id=table_id,
                entity_type="field",
                entity_id=row.id,
                action="create_field",
                before=None,
                after=result.model_dump(mode="json"),
                actor=actor,
                schema_version=table.schema_version,
                creation_request=request,
            )
        return result
    except Conflict:
        existing = _retry(db, DataField, "field", body.id, table_id, request, actor)
        if existing is None:
            raise
        return FieldResponse.model_validate(existing)


def update_field(
    db: Session, table_id: UUID, field_id: UUID, body: FieldUpdate, *, actor: Actor
) -> FieldResponse:
    require_admin(actor)
    with transaction(db):
        initial = require_field(db, table_id, field_id)
        old_target = initial.target_table_id
        requested_target = (
            body.target_table_id if "target_table_id" in body.model_fields_set else old_target
        )
        targets = {t for t in (old_target, requested_target) if t}
        table = _lock_structure(db, table_id, body.expected_schema_version, targets)
        db.refresh(initial)
        if initial.target_table_id != old_target:
            raise Conflict("SCHEMA_CHANGED", "关联配置已改变，请刷新后重试")
        if initial.status != "active":
            raise ValidationError("FIELD_ARCHIVED", "字段已归档，请先恢复")
        current = FieldResponse.model_validate(initial)
        definition = validate_field_definition(body, current=current)
        if table.table_kind == "system" and definition.field_type == "link":
            raise ValidationError(
                "SYSTEM_LINK_FORBIDDEN", "系统数据表的扩展字段暂不支持关联记录"
            )
        if definition.target_table_id and require_table(db, definition.target_table_id).table_kind == "system":
            raise ValidationError("SYSTEM_LINK_FORBIDDEN", "第一轮不支持关联到系统数据表")
        any_records = db.scalar(
            select(func.count()).select_from(DataRecord).where(DataRecord.table_id == table_id)
        )
        if any_records and (
            definition.field_type != current.field_type
            or definition.target_table_id != current.target_table_id
        ):
            raise ValidationError(
                "TYPE_CHANGE_HAS_RECORDS",
                "已有记录（含归档记录）时不能改变类型或关联目标，请新建字段",
            )
        if table.display_field_id == field_id and definition.field_type != "text":
            raise ValidationError(
                "INVALID_DISPLAY_FIELD", "请先取消此字段的显示名称设置，再改变类型"
            )
        records = _validate_existing(db, table_id, definition)
        if current.field_type in {"single_select", "multi_select"}:
            remaining = {o["id"] for o in definition.config.get("options", [])}
            for record in records:
                value = record.values.get(str(field_id))
                selected = value if isinstance(value, list) else [value]
                if any(v is not None and v not in remaining for v in selected):
                    raise ValidationError(
                        "USED_OPTION_REMOVAL", "已使用的选择项不能删除，请改为停用"
                    )
        if current.unique and not definition.unique:
            db.execute(delete(DataUniqueValue).where(DataUniqueValue.field_id == field_id))
        elif definition.unique and not current.unique:
            _reserve_unique(db, table_id, definition, records)
        before = current.model_dump(mode="json")
        for key, value in definition.model_dump().items():
            setattr(initial, key, value)
        bump(table, actor)
        initial.updated_at = utc_now()
        initial.schema_version = table.schema_version
        db.flush()
        result = FieldResponse.model_validate(initial)
        append_change(
            db,
            table_id=table_id,
            entity_type="field",
            entity_id=field_id,
            action="update_field",
            before=before,
            after=result.model_dump(mode="json"),
            actor=actor,
            schema_version=table.schema_version,
        )
    return result


def reorder_fields(
    db: Session, table_id: UUID, body: FieldReorder, *, actor: Actor
) -> list[FieldResponse]:
    require_admin(actor)
    with transaction(db):
        table = _lock_structure(db, table_id, body.expected_schema_version, [])
        fields = table_fields(db, table_id)
        active = {f.id: f for f in fields if f.status == "active"}
        if len(body.field_ids) != len(set(body.field_ids)) or set(body.field_ids) != set(active):
            raise ValidationError("INVALID_FIELD_ORDER", "请提交此表全部启用字段，不能遗漏或重复")
        before = {"field_ids": [str(f.id) for f in fields if f.status == "active"]}
        # Archived slots stay untouched; active fields occupy their old slots.
        slots = sorted(f.sort_order for f in active.values())
        bump(table, actor)
        for position, field_id in zip(slots, body.field_ids, strict=True):
            f = active[field_id]
            f.sort_order, f.schema_version, f.updated_at = position, table.schema_version, utc_now()
        db.flush()
        append_change(
            db,
            table_id=table_id,
            entity_type="table",
            entity_id=table_id,
            action="reorder_fields",
            before=before,
            after={"field_ids": [str(i) for i in body.field_ids]},
            actor=actor,
            schema_version=table.schema_version,
        )
        result = [FieldResponse.model_validate(f) for f in table_fields(db, table_id)]
    return result
