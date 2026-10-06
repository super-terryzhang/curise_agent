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
    FieldReorder,
    FieldUpdate,
    TableCreate,
    TableUpdate,
)

ADMIN = Actor(id=1, role="admin")


def service():
    return import_module("domains.dynamic_data.service")


def table(db, name="检验记录"):
    return service().create_table(db, TableCreate(id=uuid4(), name=name), actor=ADMIN)


def field(db, table_id, label="编号", kind="text", **kwargs):
    version = db.get(DataTable, table_id).schema_version
    return service().create_field(
        db,
        table_id,
        FieldCreate(
            id=uuid4(),
            label=label,
            field_type=kind,
            expected_schema_version=version,
            **kwargs,
        ),
        actor=ADMIN,
    )


def raw_record(db, table_id, values, status="active"):
    row = DataRecord(
        id=uuid4(), table_id=table_id, values=values, created_by=1, updated_by=1, status=status
    )
    db.add(row)
    db.commit()
    return row


def test_rename_preserves_field_id_and_values(data_db):
    db = data_db
    t = table(db)
    f = field(db, t.id)
    r = raw_record(db, t.id, {str(f.id): "原值"})
    changed = service().update_field(
        db, t.id, f.id, FieldUpdate(label="新名称", expected_schema_version=2), actor=ADMIN
    )
    assert changed.id == f.id
    assert db.get(DataRecord, r.id).values == {str(f.id): "原值"}
    assert db.get(DataTable, t.id).schema_version == 3
    changes = db.scalars(select(DataChange).where(DataChange.action == "update_field")).all()
    assert len(changes) == 1
    assert changes[0].before["label"] == "编号"
    assert changes[0].after["label"] == "新名称"


def test_required_and_unique_changes_check_existing_rows(data_db):
    db = data_db
    t = table(db)
    f = field(db, t.id, config={"max_length": 200})
    first = raw_record(db, t.id, {str(f.id): "重复"})
    missing = raw_record(db, t.id, {})
    with pytest.raises(ValidationError) as exc:
        service().update_field(
            db, t.id, f.id, FieldUpdate(required=True, expected_schema_version=2), actor=ADMIN
        )
    assert exc.value.issues[0].record_id == missing.id
    assert db.get(DataTable, t.id).schema_version == 2
    missing.values = {str(f.id): "重复"}
    missing.status = "archived"
    db.commit()
    with pytest.raises(Conflict):
        service().update_field(
            db, t.id, f.id, FieldUpdate(unique=True, expected_schema_version=2), actor=ADMIN
        )
    assert db.get(DataField, f.id).unique is False
    assert db.scalar(select(func.count()).select_from(DataUniqueValue)) == 0
    assert db.get(DataRecord, first.id).values == {str(f.id): "重复"}


def test_employee_cannot_change_structure(data_db):
    with pytest.raises(Forbidden):
        service().create_table(
            data_db, TableCreate(id=uuid4(), name="禁止"), actor=Actor(id=2, role="employee")
        )
    assert data_db.scalar(select(func.count()).select_from(DataTable)) == 0


def test_reorder_is_atomic_and_preserves_archived_fields(data_db):
    db = data_db
    t = table(db)
    a, b, c = [field(db, t.id, label=name) for name in ("甲", "乙", "丙")]
    db.get(DataField, b.id).status = "archived"
    db.commit()
    result = service().reorder_fields(
        db, t.id, FieldReorder(field_ids=[c.id, a.id], expected_schema_version=4), actor=ADMIN
    )
    assert [f.id for f in result if f.status == "active"] == [c.id, a.id]
    assert db.get(DataField, b.id).status == "archived"
    with pytest.raises(ValidationError):
        service().reorder_fields(
            db, t.id, FieldReorder(field_ids=[a.id, a.id], expected_schema_version=5), actor=ADMIN
        )
    assert db.get(DataTable, t.id).schema_version == 5


def test_create_retry_uses_original_request_after_rename(data_db):
    db = data_db
    body = TableCreate(id=uuid4(), name="最初名称")
    original = service().create_table(db, body, actor=ADMIN)
    service().update_table(
        db, original.id, TableUpdate(name="新名", expected_schema_version=1), actor=ADMIN
    )
    retried = service().create_table(db, body, actor=ADMIN)
    assert retried.name == "新名"
    assert db.scalar(select(func.count()).select_from(DataChange)) == 2
    with pytest.raises(Conflict):
        service().create_table(db, body.model_copy(update={"name": "不同请求"}), actor=ADMIN)
    fbody = FieldCreate(id=uuid4(), label="原字段", field_type="text", expected_schema_version=2)
    f = service().create_field(db, original.id, fbody, actor=ADMIN)
    service().update_field(
        db, original.id, f.id, FieldUpdate(label="新字段", expected_schema_version=3), actor=ADMIN
    )
    assert service().create_field(db, original.id, fbody, actor=ADMIN).label == "新字段"
    assert db.scalar(select(func.count()).select_from(DataChange)) == 4


def test_new_required_field_never_backfills_default(data_db):
    db = data_db
    t = table(db)
    f = field(db, t.id)
    r = raw_record(db, t.id, {str(f.id): "已有"})
    with pytest.raises(ValidationError):
        field(db, t.id, label="新增必填", required=True, default_value="不能补")
    assert db.get(DataRecord, r.id).values == {str(f.id): "已有"}
    assert db.get(DataTable, t.id).schema_version == 2


def test_type_target_and_display_rules(data_db):
    db = data_db
    t, target = table(db), table(db, "目标")
    f = field(db, t.id)
    n = field(db, t.id, kind="number")
    with pytest.raises(ValidationError):
        service().update_table(
            db, t.id, TableUpdate(display_field_id=n.id, expected_schema_version=3), actor=ADMIN
        )
    changed = service().update_table(
        db, t.id, TableUpdate(display_field_id=f.id, expected_schema_version=3), actor=ADMIN
    )
    assert changed.display_field_id == f.id
    service().update_field(
        db,
        t.id,
        n.id,
        FieldUpdate(
            field_type="link", target_table_id=target.id, config={}, expected_schema_version=4
        ),
        actor=ADMIN,
    )
    raw_record(db, t.id, {str(f.id): "任何记录"}, status="archived")
    with pytest.raises(ValidationError):
        service().update_field(
            db,
            t.id,
            n.id,
            FieldUpdate(
                field_type="text", target_table_id=None, config={}, expected_schema_version=5
            ),
            actor=ADMIN,
        )


def test_schema_conflict_and_history_fault_are_atomic(data_db, monkeypatch):
    db = data_db
    t = table(db)
    with pytest.raises(Conflict):
        service().update_table(
            db, t.id, TableUpdate(name="覆盖", expected_schema_version=99), actor=ADMIN
        )

    def broken(*args, **kwargs):
        raise RuntimeError("injected history failure")

    monkeypatch.setattr("domains.dynamic_data.structures.append_change", broken)
    with pytest.raises(RuntimeError):
        service().update_table(
            db, t.id, TableUpdate(name="失败名称", expected_schema_version=1), actor=ADMIN
        )
    assert db.get(DataTable, t.id).name == "检验记录"
    assert db.get(DataTable, t.id).schema_version == 1
    assert db.scalar(select(func.count()).select_from(DataChange)) == 1


def test_unique_is_canonical_and_claims_include_archived_rows(data_db):
    db = data_db
    t = table(db)
    f = field(db, t.id, kind="number")
    a = raw_record(db, t.id, {str(f.id): "1.00"})
    b = raw_record(db, t.id, {str(f.id): "2"}, status="archived")
    service().update_field(
        db, t.id, f.id, FieldUpdate(unique=True, expected_schema_version=2), actor=ADMIN
    )
    assert {(claim.record_id, claim.value) for claim in db.scalars(select(DataUniqueValue))} == {
        (a.id, "1"),
        (b.id, "2"),
    }
    service().update_field(
        db, t.id, f.id, FieldUpdate(unique=False, expected_schema_version=3), actor=ADMIN
    )
    assert db.scalar(select(func.count()).select_from(DataUniqueValue)) == 0


def test_field_limit_includes_archived_fields(data_db):
    db = data_db
    t = table(db)
    db.add_all(
        [
            DataField(
                id=uuid4(),
                table_id=t.id,
                label=f"字段{i}",
                field_type="text",
                sort_order=i,
                status="archived",
            )
            for i in range(100)
        ]
    )
    db.commit()
    with pytest.raises(ValidationError):
        field(db, t.id)
    assert db.get(DataTable, t.id).schema_version == 1
    assert db.scalar(select(func.count()).select_from(DataField)) == 100


def test_option_deactivation_preserves_but_used_option_removal_rejects(data_db):
    db = data_db
    t = table(db)
    option = str(uuid4())
    f = field(db, t.id, kind="single_select", config={"options": [{"id": option, "label": "旧名"}]})
    raw_record(db, t.id, {str(f.id): option})
    changed = service().update_field(
        db,
        t.id,
        f.id,
        FieldUpdate(
            config={"options": [{"id": option, "label": "停用名", "active": False}]},
            expected_schema_version=2,
        ),
        actor=ADMIN,
    )
    assert changed.config["options"][0]["active"] is False
    with pytest.raises(ValidationError):
        service().update_field(
            db,
            t.id,
            f.id,
            FieldUpdate(config={"options": []}, expected_schema_version=3),
            actor=ADMIN,
        )
    assert db.get(DataTable, t.id).schema_version == 3


def test_required_link_validation_reads_authoritative_links(data_db):
    db = data_db
    source, target = table(db), table(db, "目标")
    f = field(db, source.id, kind="link", target_table_id=target.id)
    s = raw_record(db, source.id, {})
    t = raw_record(db, target.id, {})
    db.add(
        DataLink(
            table_id=source.id,
            record_id=s.id,
            field_id=f.id,
            target_table_id=target.id,
            target_record_id=t.id,
        )
    )
    db.commit()
    updated = service().update_field(
        db, source.id, f.id, FieldUpdate(required=True, expected_schema_version=2), actor=ADMIN
    )
    assert updated.required is True
    assert db.get(DataRecord, s.id).values == {}
