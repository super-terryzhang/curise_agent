from uuid import uuid4

import pytest
from sqlalchemy import func, select

from domains.dynamic_data import service as s
from domains.dynamic_data.errors import Conflict, ValidationError
from domains.dynamic_data.models import (
    DataChange,
    DataField,
    DataLink,
    DataRecord,
    DataTable,
    DataUniqueValue,
)
from domains.dynamic_data.schemas import (
    Actor,
    FieldCreate,
    RecordAction,
    RecordCreate,
    SchemaAction,
    TableCreate,
)

A = Actor(id=1, role="admin")


def table(db):
    return s.create_table(db, TableCreate(id=uuid4(), name="生命周期"), actor=A)


def field(db, t, kind="text", label="字段", **kwargs):
    return s.create_field(
        db,
        t.id,
        FieldCreate(
            id=uuid4(),
            label=label,
            field_type=kind,
            expected_schema_version=db.get(DataTable, t.id).schema_version,
            **kwargs,
        ),
        actor=A,
    )


def record(db, t, values):
    return s.create_record(
        db,
        t.id,
        RecordCreate(
            id=uuid4(), schema_version=db.get(DataTable, t.id).schema_version, values=values
        ),
        actor=A,
    )


def schema(db, t):
    return SchemaAction(expected_schema_version=db.get(DataTable, t.id).schema_version)


def action(db, t, r):
    return RecordAction(
        schema_version=db.get(DataTable, t.id).schema_version,
        expected_revision=db.get(DataRecord, r.id).revision,
    )


def test_archived_data_keeps_values_unique_claims_and_history(data_db):
    db = data_db
    t = table(db)
    f = field(db, t, unique=True)
    r = record(db, t, {str(f.id): "保留"})
    s.set_record_status(db, t.id, r.id, action(db, t, r), active=False, actor=A)
    assert db.get(DataRecord, r.id).values == {str(f.id): "保留"}
    assert db.scalar(select(func.count()).select_from(DataUniqueValue)) == 1
    with pytest.raises(Conflict):
        record(db, t, {str(f.id): "保留"})
    s.set_record_status(db, t.id, r.id, action(db, t, r), active=True, actor=A)
    assert db.get(DataRecord, r.id).status == "active"
    assert (
        db.scalar(select(func.count()).select_from(DataChange).where(DataChange.entity_id == r.id))
        == 3
    )
    s.set_field_status(db, t.id, f.id, schema(db, t), active=False, actor=A)
    assert db.get(DataRecord, r.id).values == {str(f.id): "保留"}
    assert db.scalar(select(func.count()).select_from(DataUniqueValue)) == 1


def test_target_archive_is_blocked_then_source_archive_allows_it(data_db):
    db = data_db
    source, target = table(db), table(db)
    field(db, target)
    f = field(db, source, "link", target_table_id=target.id)
    dest = record(db, target, {})
    r = record(db, source, {str(f.id): str(dest.id)})
    with pytest.raises(ValidationError) as exc:
        s.set_record_status(db, target.id, dest.id, action(db, target, dest), active=False, actor=A)
    assert exc.value.issues[0].record_id == r.id
    with pytest.raises(ValidationError):
        s.set_table_status(db, target.id, schema(db, target), active=False, actor=A)
    s.set_table_status(db, source.id, schema(db, source), active=False, actor=A)
    s.set_record_status(db, target.id, dest.id, action(db, target, dest), active=False, actor=A)
    assert db.scalar(select(func.count()).select_from(DataLink)) == 1


def test_restore_with_invalid_target_is_rejected_atomically(data_db):
    db = data_db
    source, target = table(db), table(db)
    field(db, target)
    f = field(db, source, "link", target_table_id=target.id)
    dest = record(db, target, {})
    record(db, source, {str(f.id): str(dest.id)})
    s.set_table_status(db, source.id, schema(db, source), active=False, actor=A)
    s.set_record_status(db, target.id, dest.id, action(db, target, dest), active=False, actor=A)
    count = db.scalar(select(func.count()).select_from(DataChange))
    version = db.get(DataTable, source.id).schema_version
    with pytest.raises(ValidationError):
        s.set_table_status(db, source.id, schema(db, source), active=True, actor=A)
    assert db.get(DataTable, source.id).status == "archived"
    assert db.get(DataTable, source.id).schema_version == version
    assert db.scalar(select(func.count()).select_from(DataChange)) == count


def test_field_restore_rechecks_required_and_clears_display_on_archive(data_db):
    db = data_db
    t = table(db)
    f = field(db, t, required=True)
    from domains.dynamic_data.schemas import TableUpdate

    s.update_table(db, t.id, TableUpdate(expected_schema_version=2, display_field_id=f.id), actor=A)
    r = record(db, t, {str(f.id): "合法"})
    s.set_field_status(db, t.id, f.id, schema(db, t), active=False, actor=A)
    assert db.get(DataTable, t.id).display_field_id is None
    db.get(DataRecord, r.id).values = {}
    db.commit()
    with pytest.raises(ValidationError):
        s.set_field_status(db, t.id, f.id, schema(db, t), active=True, actor=A)
    assert db.get(DataField, f.id).status == "archived"


def test_field_restore_rejects_an_active_normalized_label_duplicate(data_db):
    db = data_db
    t = table(db)
    archived = field(db, t, label="业务分类")
    s.set_field_status(db, t.id, archived.id, schema(db, t), active=False, actor=A)
    field(db, t, label=" 业务分类 ")

    with pytest.raises(Conflict, match="字段名称"):
        s.set_field_status(db, t.id, archived.id, schema(db, t), active=True, actor=A)

    assert db.get(DataField, archived.id).status == "archived"


def test_table_can_restore_without_fields_but_record_cannot(data_db):
    db = data_db
    t = table(db)
    f = field(db, t)
    r = record(db, t, {str(f.id): "旧值"})
    s.set_record_status(db, t.id, r.id, action(db, t, r), active=False, actor=A)
    s.set_field_status(db, t.id, f.id, schema(db, t), active=False, actor=A)
    s.set_table_status(db, t.id, schema(db, t), active=False, actor=A)
    assert s.set_table_status(db, t.id, schema(db, t), active=True, actor=A).status == "active"
    with pytest.raises(ValidationError):
        s.set_record_status(db, t.id, r.id, action(db, t, r), active=True, actor=A)


def test_table_restore_with_hidden_rows_and_no_fields(data_db):
    db = data_db
    t = table(db)
    f = field(db, t)
    record(db, t, {str(f.id): "隐藏值"})
    s.set_field_status(db, t.id, f.id, schema(db, t), active=False, actor=A)
    s.set_table_status(db, t.id, schema(db, t), active=False, actor=A)
    assert s.set_table_status(db, t.id, schema(db, t), active=True, actor=A).status == "active"
