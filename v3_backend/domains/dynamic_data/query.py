"""Bound, typed SQL queries with stable pagination and batched associations."""

from uuid import UUID

from sqlalchemy import DateTime, Numeric, String, case, cast, exists, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from .errors import NotFound, ValidationError
from .models import DataChange, DataField, DataLink, DataRecord, DataTable
from .records import link_labels, record_response
from .repository import require_field, require_record, require_table, table_fields
from .schemas import (
    Actor,
    ChangeQuery,
    ChangeResponse,
    FieldResponse,
    Page,
    RecordQuery,
    RecordResponse,
    TableResponse,
)
from .system_tables import (
    core_field_count,
    count_system_records,
    get_system_record,
    list_system_records,
    system_fields,
    visible_source_id_statement,
)
from .validation import normalize_value


def _page(page, page_size):
    if page < 1 or not 1 <= page_size <= 100:
        raise ValidationError("INVALID_PAGE", "页码至少为 1，每页最多 100 条")


def _counts(db, table_ids):
    fields = dict(
        db.execute(
            select(DataField.table_id, func.count())
            .where(DataField.table_id.in_(table_ids), DataField.status == "active")
            .group_by(DataField.table_id)
        ).all()
    )
    records = dict(
        db.execute(
            select(DataRecord.table_id, func.count())
            .where(DataRecord.table_id.in_(table_ids), DataRecord.status == "active")
            .group_by(DataRecord.table_id)
        ).all()
    )
    return fields, records


def list_tables(
    db: Session, *, status: str, page: int, page_size: int, actor: Actor | None = None
) -> Page[TableResponse]:
    _page(page, page_size)
    if status not in {"active", "archived"}:
        raise ValidationError("INVALID_STATUS", "请选择启用或归档状态")
    query = select(DataTable).where(DataTable.status == status)
    if status == "archived":
        query = query.where(DataTable.table_kind == "user")
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    tables = list(
        db.scalars(
            query.order_by(DataTable.updated_at.desc(), DataTable.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    fc, rc = _counts(db, [t.id for t in tables]) if tables else ({}, {})
    for table in tables:
        if table.table_kind == "system":
            fc[table.id] = fc.get(table.id, 0) + core_field_count(table)
            rc[table.id] = count_system_records(db, table, actor=actor)
    return Page(
        items=[
            TableResponse.model_validate(t).model_copy(
                update={"field_count": fc.get(t.id, 0), "record_count": rc.get(t.id, 0)}
            )
            for t in tables
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


def get_table(db: Session, table_id: UUID, *, actor: Actor | None = None) -> TableResponse:
    table = require_table(db, table_id, active=False)
    fc, rc = _counts(db, [table_id])
    if table.table_kind == "system":
        fc[table_id] = fc.get(table_id, 0) + core_field_count(table)
        rc[table_id] = count_system_records(db, table, actor=actor)
    return TableResponse.model_validate(table).model_copy(
        update={"field_count": fc.get(table_id, 0), "record_count": rc.get(table_id, 0)}
    )


def list_fields(db: Session, table_id: UUID, *, include_archived: bool) -> list[FieldResponse]:
    table = require_table(db, table_id, active=False)
    core = system_fields(table) if table.table_kind == "system" else []
    extensions = [
        FieldResponse.model_validate(f)
        for f in table_fields(db, table_id)
        if include_archived or f.status == "active"
    ]
    return core + extensions


def get_record(
    db: Session, table_id: UUID, record_id: UUID | str, *, actor: Actor | None = None
) -> RecordResponse:
    table = require_table(db, table_id, active=False)
    if table.table_kind == "system":
        return get_system_record(db, table, record_id, actor=actor)
    try:
        user_record_id = UUID(str(record_id))
    except ValueError as exc:
        raise NotFound("RECORD_NOT_FOUND", "记录不存在于此数据表") from exc
    return record_response(db, require_record(db, table_id, user_record_id))


def _scalar(db, field):
    value = DataRecord.values[str(field.id)]
    if field.field_type == "number":
        return cast(value.as_string(), Numeric(30, 6))
    if field.field_type == "boolean":
        return value.as_boolean()
    if field.field_type == "datetime":
        return (
            func.julianday(value.as_string())
            if db.bind.dialect.name == "sqlite"
            else cast(value.as_string(), DateTime(timezone=True))
        )
    return value.as_string()


def _condition(db, field, item):
    kind, operator = field.field_type, item.operator
    allowed = {
        "text": {"eq", "contains"},
        "number": {"eq", "lt", "lte", "gt", "gte"},
        "date": {"eq", "lt", "lte", "gt", "gte"},
        "datetime": {"eq", "lt", "lte", "gt", "gte"},
        "boolean": {"eq"},
        "single_select": {"eq"},
        "multi_select": {"contains"},
        "link": {"eq"},
    }
    raw = DataRecord.values[str(field.id)]
    if kind == "link":
        links = select(DataLink.record_id).where(
            DataLink.table_id == field.table_id, DataLink.field_id == field.id
        )
        if operator == "is_empty":
            return DataRecord.id.not_in(links)
        if operator != "eq":
            raise ValidationError("INVALID_FILTER_OPERATOR", "关联字段只支持等于或为空")
        value = normalize_value(field.model_copy(update={"required": False}), item.value)
        if value is None:
            raise ValidationError("EMPTY_FILTER_VALUE", "空值请使用“为空”条件")
        return DataRecord.id.in_(links.where(DataLink.target_record_id == UUID(value)))
    if operator == "is_empty":
        empty = raw.as_string().is_(None)
        if kind == "text":
            return or_(empty, raw.as_string() == "")
        if kind == "multi_select":
            length = (
                func.json_array_length(raw)
                if db.bind.dialect.name == "sqlite"
                else case(
                    (func.jsonb_typeof(raw) == "array", func.jsonb_array_length(raw)), else_=None
                )
            )
            return or_(empty, length == 0)
        return empty
    if operator not in allowed[kind]:
        raise ValidationError("INVALID_FILTER_OPERATOR", "此字段类型不支持所选筛选条件")
    filter_field = field.model_copy(
        update={
            "required": False,
            "field_type": "single_select" if kind == "multi_select" else kind,
        }
    )
    value = normalize_value(
        filter_field,
        item.value,
        allow_inactive_option_ids={str(o["id"]) for o in field.config.get("options", [])},
    )
    if value is None:
        raise ValidationError("EMPTY_FILTER_VALUE", "空值请使用“为空”条件")
    if kind == "multi_select":
        if db.bind.dialect.name == "postgresql":
            return raw.op("@>")(literal([value], type_=JSONB))
        elements = func.json_each(raw).table_valued("value")
        return exists(select(1).select_from(elements).where(elements.c.value == value))
    expression = _scalar(db, field)
    if kind == "number":
        from decimal import Decimal

        value = Decimal(value)
    elif kind == "datetime":
        from datetime import datetime

        value = (
            func.julianday(value)
            if db.bind.dialect.name == "sqlite"
            else datetime.fromisoformat(value.replace("Z", "+00:00"))
        )
    if operator == "contains":
        return expression.contains(value, autoescape=True)
    return {
        "eq": lambda: expression == value,
        "lt": lambda: expression < value,
        "lte": lambda: expression <= value,
        "gt": lambda: expression > value,
        "gte": lambda: expression >= value,
    }[operator]()


def _record_page(db, table, statement, page, page_size):
    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    rows = list(db.scalars(statement.offset((page - 1) * page_size).limit(page_size)))
    values = {r.id: dict(r.values) for r in rows}
    links = []
    if rows:
        links = list(
            db.scalars(
                select(DataLink).where(
                    DataLink.table_id == table.id, DataLink.record_id.in_(values)
                )
            )
        )
        for link in links:
            values[link.record_id][str(link.field_id)] = str(link.target_record_id)
    labels = link_labels(db, links)
    return Page(
        items=[record_response(db, r, values[r.id], labels.get(r.id, {})) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


def list_records(
    db: Session, table_id: UUID, query: RecordQuery, *, actor: Actor | None = None
) -> Page[RecordResponse]:
    table = require_table(db, table_id, active=False)
    if table.table_kind == "system":
        return list_system_records(db, table, query, actor=actor)
    if query.q:
        raise ValidationError("UNSUPPORTED_USER_QUERY", "普通数据表请使用字段筛选")
    fields = {
        f.id: FieldResponse.model_validate(f)
        for f in table_fields(db, table_id)
        if f.status == "active"
    }
    statement = select(DataRecord).where(
        DataRecord.table_id == table_id, DataRecord.status == query.status
    )
    for item in query.filters:
        field = fields.get(item.field_id)
        if field is None:
            raise ValidationError("UNKNOWN_FILTER_FIELD", "筛选字段不存在或已归档")
        statement = statement.where(_condition(db, field, item))
    if query.sort_field_id:
        field = fields.get(query.sort_field_id)
        if field is None or field.field_type in {"link", "multi_select"}:
            raise ValidationError("INVALID_SORT_FIELD", "排序必须选择启用的标量字段")
        expression = _scalar(db, field)
    else:
        expression = DataRecord.created_at
    expression = expression.asc() if query.sort_direction == "asc" else expression.desc()
    return _record_page(
        db,
        table,
        statement.order_by(expression.nulls_last(), DataRecord.id),
        query.page,
        query.page_size,
    )


def list_changes(
    db: Session, table_id: UUID, query: ChangeQuery, *, actor: Actor | None = None
) -> Page[ChangeResponse]:
    table = require_table(db, table_id, active=False)
    statement = select(DataChange).where(DataChange.table_id == table_id)
    if query.entity_type:
        statement = statement.where(DataChange.entity_type == query.entity_type)
    if table.table_kind != "system" and query.record_id:
        try:
            record_id = UUID(str(query.record_id))
        except ValueError as exc:
            raise NotFound("RECORD_NOT_FOUND", "记录不存在于此数据表") from exc
        statement = statement.where(DataChange.record_id == record_id)
    if table.table_kind == "system":
        visible_sources = visible_source_id_statement(table, actor=actor)
        visible_anchors = select(DataRecord.id).where(
            DataRecord.table_id == table_id,
            DataRecord.source_record_id.in_(visible_sources),
        )
        if query.record_id:
            visible_anchors = visible_anchors.where(
                DataRecord.source_record_id == str(query.record_id)
            )
            statement = statement.where(DataChange.record_id.in_(visible_anchors))
        else:
            statement = statement.where(
                or_(
                    DataChange.record_id.is_(None),
                    DataChange.record_id.in_(visible_anchors),
                )
            )
        total = db.scalar(select(func.count()).select_from(statement.subquery()))
        rows = list(
            db.scalars(
                statement.order_by(DataChange.created_at.desc(), DataChange.id.desc())
                .offset((query.page - 1) * query.page_size)
                .limit(query.page_size)
            )
        )
        anchor_ids = {row.record_id for row in rows if row.record_id is not None}
        anchors = {
            row.id: row
            for row in db.scalars(select(DataRecord).where(DataRecord.id.in_(anchor_ids)))
        }
        items = []
        for row in rows:
            if row.record_id is None:
                items.append(ChangeResponse.model_validate(row))
                continue
            anchor = anchors.get(row.record_id)
            source_id = anchor.source_record_id if anchor else None
            if source_id is None:
                continue
            response = ChangeResponse.model_validate(row)
            items.append(
                response.model_copy(
                    update={
                        "record_id": source_id,
                        "entity_id": source_id
                        if row.entity_type == "record"
                        else response.entity_id,
                    }
                )
            )
        return Page(
            items=items,
            total=int(total or 0),
            page=query.page,
            page_size=query.page_size,
        )
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = db.scalars(
        statement.order_by(DataChange.created_at.desc(), DataChange.id.desc())
        .offset((query.page - 1) * query.page_size)
        .limit(query.page_size)
    )
    return Page(
        items=[ChangeResponse.model_validate(r) for r in rows],
        total=total,
        page=query.page,
        page_size=query.page_size,
    )


def search_link_targets(
    db: Session, table_id: UUID, field_id: UUID, *, q: str, page: int, page_size: int
) -> Page[RecordResponse]:
    _page(page, page_size)
    require_table(db, table_id)
    field = require_field(db, table_id, field_id)
    if field.field_type != "link" or field.status != "active":
        raise ValidationError("INVALID_LINK_FIELD", "请选择启用的关联字段")
    if len(q) > 200:
        raise ValidationError("SEARCH_TOO_LONG", "搜索内容不能超过 200 字符")
    target = require_table(db, field.target_table_id)
    statement = select(DataRecord).where(
        DataRecord.table_id == target.id, DataRecord.status == "active"
    )
    if q:
        id_text = cast(DataRecord.id, String)
        if db.bind.dialect.name == "sqlite":
            id_text = func.replace(id_text, "-", "")
        conditions = [
            id_text.contains(
                q.replace("-", "") if db.bind.dialect.name == "sqlite" else q, autoescape=True
            )
        ]
        if target.display_field_id:
            conditions.append(
                DataRecord.values[str(target.display_field_id)]
                .as_string()
                .contains(q, autoescape=True)
            )
        statement = statement.where(or_(*conditions))
    return _record_page(
        db, target, statement.order_by(DataRecord.created_at, DataRecord.id), page, page_size
    )
