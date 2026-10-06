from importlib import import_module
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from domains.dynamic_data.errors import Conflict, Forbidden, ValidationError
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
    FieldUpdate,
    RecordCreate,
    RecordUpdate,
    TableCreate,
)

ADMIN = Actor(id=1, role="admin")
WRITER = Actor(id=2, role="employee")


def service():
    return import_module("domains.dynamic_data.service")


def table(db):
    return service().create_table(db, TableCreate(id=uuid4(), name="记录测试"), actor=ADMIN)


def field(db, table_id, kind="text", **kwargs):
    return service().create_field(
        db,
        table_id,
        FieldCreate(
            id=uuid4(),
            label="值",
            field_type=kind,
            expected_schema_version=db.get(DataTable, table_id).schema_version,
            **kwargs,
        ),
        actor=ADMIN,
    )


def create(db, table_id, values, record_id=None):
    return service().create_record(
        db,
        table_id,
        RecordCreate(
            id=record_id or uuid4(),
            schema_version=db.get(DataTable, table_id).schema_version,
            values=values,
        ),
        actor=WRITER,
    )


def snapshot(db):
    return {
        model.__tablename__: [
            {c.name: getattr(row, c.name) for c in model.__table__.columns}
            for row in db.scalars(select(model))
        ]
        for model in (DataRecord, DataLink, DataUniqueValue, DataChange)
    }


def test_patch_is_partial_but_validates_full_record(data_db):
    db = data_db
    t = table(db)
    a, b = field(db, t.id, required=True), field(db, t.id, "boolean", required=True)
    r = create(db, t.id, {str(a.id): "原值", str(b.id): False})
    updated = service().update_record(
        db,
        t.id,
        r.id,
        RecordUpdate(expected_revision=1, schema_version=3, values={str(a.id): "新值"}),
        actor=WRITER,
    )
    assert updated.values == {str(a.id): "新值", str(b.id): False}
    assert updated.revision == 2
    before = snapshot(db)
    with pytest.raises(ValidationError):
        service().update_record(
            db,
            t.id,
            r.id,
            RecordUpdate(expected_revision=2, schema_version=3, values={str(b.id): None}),
            actor=WRITER,
        )
    assert snapshot(db) == before


def test_duplicate_value_rolls_back_record_links_and_history(data_db):
    db = data_db
    t, target = table(db), table(db)
    name = field(db, t.id, unique=True)
    link = field(db, t.id, "link", target_table_id=target.id)
    target_name = field(db, target.id)
    target_r = create(db, target.id, {str(target_name.id): "目标"})
    create(db, t.id, {str(name.id): "唯一"})
    before = snapshot(db)
    with pytest.raises(Conflict) as exc:
        create(db, t.id, {str(name.id): "唯一", str(link.id): str(target_r.id)})
    assert snapshot(db) == before
    assert exc.value.issues[0].field_id == name.id


def test_wrong_target_table_and_archived_target_are_rejected(data_db):
    db = data_db
    t, target, other = table(db), table(db), table(db)
    link = field(db, t.id, "link", target_table_id=target.id)
    for dest in (target, other):
        field(db, dest.id)
    wrong = create(db, other.id, {})
    valid = create(db, target.id, {})
    before = snapshot(db)
    with pytest.raises(ValidationError):
        create(db, t.id, {str(link.id): str(wrong.id)})
    assert snapshot(db) == before
    db.get(DataRecord, valid.id).status = "archived"
    db.commit()
    before = snapshot(db)
    with pytest.raises(ValidationError):
        create(db, t.id, {str(link.id): str(valid.id)})
    assert snapshot(db) == before


def test_same_create_uuid_after_later_edit_uses_original_request(data_db):
    db = data_db
    t = table(db)
    f = field(db, t.id, default_value="初始默认")
    body = RecordCreate(id=uuid4(), schema_version=2, values={})
    original = service().create_record(db, t.id, body, actor=WRITER)
    edited = service().update_record(
        db,
        t.id,
        original.id,
        RecordUpdate(expected_revision=1, schema_version=2, values={str(f.id): "编辑值"}),
        actor=WRITER,
    )
    service().update_field(
        db,
        t.id,
        f.id,
        FieldUpdate(default_value="后来默认", expected_schema_version=2),
        actor=ADMIN,
    )
    retried = service().create_record(db, t.id, body, actor=WRITER)
    assert retried.id == original.id
    assert retried.revision == edited.revision
    assert retried.values[str(f.id)] == "编辑值"
    assert (
        db.scalar(
            select(func.count()).select_from(DataChange).where(DataChange.action == "create_record")
        )
        == 1
    )
    with pytest.raises(Conflict):
        service().create_record(
            db, t.id, body.model_copy(update={"values": {str(f.id): "其他"}}), actor=WRITER
        )
    with pytest.raises(Conflict):
        service().create_record(db, t.id, body, actor=ADMIN)


def test_history_failure_revision_conflict_and_schema_time(data_db, monkeypatch):
    db = data_db
    t = table(db)
    f = field(db, t.id)
    structure_time = db.get(DataTable, t.id).updated_at
    r = create(db, t.id, {str(f.id): "旧值"})
    assert db.get(DataTable, t.id).updated_at == structure_time
    before = snapshot(db)

    def broken(*args, **kwargs):
        raise RuntimeError("history fault")

    monkeypatch.setattr("domains.dynamic_data.records.append_change", broken)
    with pytest.raises(RuntimeError):
        service().update_record(
            db,
            t.id,
            r.id,
            RecordUpdate(expected_revision=1, schema_version=2, values={str(f.id): "失败值"}),
            actor=WRITER,
        )
    assert snapshot(db) == before
    with pytest.raises(Conflict):
        service().update_record(
            db,
            t.id,
            r.id,
            RecordUpdate(expected_revision=99, schema_version=2, values={}),
            actor=WRITER,
        )
    with pytest.raises(Conflict):
        service().update_record(
            db,
            t.id,
            r.id,
            RecordUpdate(expected_revision=1, schema_version=99, values={}),
            actor=WRITER,
        )
    with pytest.raises(Forbidden):
        service().create_record(
            db,
            t.id,
            RecordCreate(id=uuid4(), schema_version=2, values={}),
            actor=Actor(id=3, role="viewer"),
        )


def test_links_are_not_duplicated_in_values_and_null_clears(data_db):
    db = data_db
    t, target = table(db), table(db)
    target_name = field(db, target.id)
    link = field(db, t.id, "link", target_table_id=target.id)
    dest = create(db, target.id, {str(target_name.id): "目标"})
    r = create(db, t.id, {str(link.id): str(dest.id)})
    assert r.values[str(link.id)] == str(dest.id)
    assert db.get(DataRecord, r.id).values == {}
    updated = service().update_record(
        db,
        t.id,
        r.id,
        RecordUpdate(expected_revision=1, schema_version=2, values={str(link.id): None}),
        actor=WRITER,
    )
    assert updated.values.get(str(link.id)) is None
    assert db.scalar(select(func.count()).select_from(DataLink)) == 0


def test_inactive_selection_preserved_and_archived_field_not_editable(data_db):
    db = data_db
    t = table(db)
    option = str(uuid4())
    f = field(db, t.id, "single_select", config={"options": [{"id": option, "label": "旧项"}]})
    text = field(db, t.id)
    r = create(db, t.id, {str(f.id): option, str(text.id): "说明"})
    service().update_field(
        db,
        t.id,
        f.id,
        FieldUpdate(
            expected_schema_version=3,
            config={"options": [{"id": option, "label": "已停用", "active": False}]},
        ),
        actor=ADMIN,
    )
    updated = service().update_record(
        db,
        t.id,
        r.id,
        RecordUpdate(expected_revision=1, schema_version=4, values={str(text.id): "新说明"}),
        actor=WRITER,
    )
    assert updated.values[str(f.id)] == option
    before = snapshot(db)
    with pytest.raises(ValidationError):
        create(db, t.id, {str(f.id): option})
    assert snapshot(db) == before
    db.get(DataField, text.id).status = "archived"
    db.commit()
    before = snapshot(db)
    with pytest.raises(ValidationError):
        service().update_record(
            db,
            t.id,
            r.id,
            RecordUpdate(expected_revision=2, schema_version=4, values={str(text.id): "禁止"}),
            actor=WRITER,
        )
    assert snapshot(db) == before


def test_relation_history_keeps_target_label_at_save(data_db):
    db = data_db
    t, target = table(db), table(db)
    name = field(db, target.id)
    from domains.dynamic_data.schemas import TableUpdate

    service().update_table(
        db, target.id, TableUpdate(expected_schema_version=2, display_field_id=name.id), actor=ADMIN
    )
    link = field(db, t.id, "link", target_table_id=target.id)
    dest = create(db, target.id, {str(name.id): "原目标名"})
    row = create(db, t.id, {str(link.id): str(dest.id)})
    service().update_record(
        db,
        target.id,
        dest.id,
        RecordUpdate(expected_revision=1, schema_version=3, values={str(name.id): "后来名称"}),
        actor=WRITER,
    )
    change = db.scalar(
        select(DataChange).where(
            DataChange.entity_id == row.id, DataChange.action == "create_record"
        )
    )
    assert change.display_snapshot["links"][str(dest.id)]["display_label"] == "原目标名"


def test_no_fields_and_archived_records_reject_writes(data_db):
    db = data_db
    t = table(db)
    with pytest.raises(ValidationError):
        create(db, t.id, {})
    field(db, t.id)
    r = create(db, t.id, {})
    db.get(DataRecord, r.id).status = "archived"
    db.commit()
    before = snapshot(db)
    with pytest.raises(ValidationError):
        service().update_record(
            db,
            t.id,
            r.id,
            RecordUpdate(expected_revision=1, schema_version=2, values={}),
            actor=WRITER,
        )
    assert snapshot(db) == before
