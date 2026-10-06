"""Scoped queries, deterministic locking and transaction rollback boundaries."""

from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .errors import Conflict, NotFound, ValidationError
from .models import DataChange, DataField, DataRecord, DataTable


@contextmanager
def transaction(db: Session):
    try:
        yield
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("DATA_CONFLICT", "数据违反唯一或关联约束，请刷新后检查") from exc
    except Exception:
        db.rollback()
        raise


def require_table(db: Session, table_id: UUID, *, active: bool = True) -> DataTable:
    row = db.get(DataTable, table_id)
    if row is None:
        raise NotFound("TABLE_NOT_FOUND", "数据表不存在")
    if active and row.status != "active":
        raise ValidationError("TABLE_ARCHIVED", "数据表已归档，请先恢复")
    return row


def require_field(db: Session, table_id: UUID, field_id: UUID) -> DataField:
    row = db.get(DataField, field_id)
    if row is None or row.table_id != table_id:
        raise NotFound("FIELD_NOT_FOUND", "字段不存在于此数据表")
    return row


def lock_tables(db: Session, table_ids: list[UUID], *, exclusive_ids: set[UUID]) -> list[DataTable]:
    rows = []
    for table_id in sorted(set(table_ids), key=str):
        row = db.scalar(
            select(DataTable)
            .where(DataTable.id == table_id)
            .with_for_update(read=table_id not in exclusive_ids)
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFound("TABLE_NOT_FOUND", "来源或目标数据表不存在")
        rows.append(row)
    return rows


def lock_records(db: Session, record_ids: list[UUID], *, write_ids: set[UUID]) -> list[DataRecord]:
    rows = []
    for record_id in sorted(set(record_ids), key=str):
        row = db.scalar(
            select(DataRecord)
            .where(DataRecord.id == record_id)
            .with_for_update(read=record_id not in write_ids)
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFound("RECORD_NOT_FOUND", "来源或关联记录不存在")
        rows.append(row)
    return rows


def table_fields(db: Session, table_id: UUID) -> list[DataField]:
    return list(
        db.scalars(
            select(DataField)
            .where(DataField.table_id == table_id)
            .order_by(DataField.sort_order, DataField.id)
        )
    )


def creation_change(db: Session, entity_type: str, entity_id: UUID) -> DataChange | None:
    return db.scalar(
        select(DataChange).where(
            DataChange.entity_type == entity_type,
            DataChange.entity_id == entity_id,
            DataChange.creation_request.is_not(None),
        )
    )
