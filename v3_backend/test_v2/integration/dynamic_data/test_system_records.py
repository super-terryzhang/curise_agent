"""System tables protect core structure and persist extension values atomically."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from domains.document.models import Document, DocumentFolder
from domains.dynamic_data import service as s
from domains.dynamic_data.errors import Conflict, NotFound, ValidationError
from domains.dynamic_data.models import DataChange, DataRecord, DataTable
from domains.dynamic_data.schemas import (
    Actor,
    ChangeQuery,
    FieldCreate,
    FieldReorder,
    FieldUpdate,
    RecordQuery,
    SchemaAction,
    SystemRecordUpdate,
    TableCreate,
    TableUpdate,
)
from domains.identity.models import User
from domains.masterdata.models import Category, Country, Port, Product, Supplier
from domains.orders.models import Order, OrderGroup
from infrastructure.db.base import Base

PRODUCTS_ID = UUID("025588dd-ae63-5607-9e78-1179a500ed6e")
ORDERS_ID = UUID("0bc67ecc-ccd3-53b5-8327-74f4b482b7b4")
ADMIN = Actor(id=1, role="admin")
EMPLOYEE = Actor(id=10, role="employee")


def install_core_schema(db):
    _ = (Document, User, OrderGroup)
    Base.metadata.create_all(
        db.bind,
        tables=[
            User.__table__,
            DocumentFolder.__table__,
            Document.__table__,
            OrderGroup.__table__,
            Country.__table__,
            Category.__table__,
            Port.__table__,
            Supplier.__table__,
            Product.__table__,
            Order.__table__,
        ],
    )


def system_table(db, table_id, key, name):
    row = DataTable(
        id=table_id,
        name=name,
        table_kind="system",
        system_key=key,
        created_by=0,
        updated_by=0,
        created_at=datetime(2026, 10, 6, tzinfo=UTC),
        updated_at=datetime(2026, 10, 6, tzinfo=UTC),
    )
    db.add(row)
    db.commit()
    return row


def extension_field(db, table_id, *, kind="text", target_table_id=None):
    table = db.get(DataTable, table_id)
    return s.create_field(
        db,
        table_id,
        FieldCreate(
            id=uuid4(),
            label="内部备注",
            field_type=kind,
            target_table_id=target_table_id,
            expected_schema_version=table.schema_version,
        ),
        actor=ADMIN,
    )


def test_system_structure_is_locked_but_extensions_remain_configurable(data_db):
    db = data_db
    install_core_schema(db)
    table = system_table(db, PRODUCTS_ID, "products", "产品")
    user_table = s.create_table(
        db, TableCreate(id=uuid4(), name="人工检查表"), actor=ADMIN
    )

    with pytest.raises(ValidationError):
        s.update_table(
            db,
            table.id,
            TableUpdate(name="伪造名称", expected_schema_version=1),
            actor=ADMIN,
        )
    with pytest.raises(ValidationError):
        s.set_table_status(
            db,
            table.id,
            SchemaAction(expected_schema_version=1),
            active=False,
            actor=ADMIN,
        )
    with pytest.raises(ValidationError):
        extension_field(db, table.id, kind="link", target_table_id=user_table.id)
    with pytest.raises(ValidationError):
        extension_field(db, user_table.id, kind="link", target_table_id=table.id)

    first = extension_field(db, table.id)
    second = extension_field(db, table.id, kind="boolean")
    reordered = s.reorder_fields(
        db,
        table.id,
        FieldReorder(
            field_ids=[second.id, first.id],
            expected_schema_version=db.get(DataTable, table.id).schema_version,
        ),
        actor=ADMIN,
    )
    assert [field.id for field in reordered if field.status == "active"] == [
        second.id,
        first.id,
    ]
    core = next(
        field
        for field in s.list_fields(db, table.id, include_archived=True)
        if field.source == "core"
    )
    with pytest.raises(NotFound):
        s.update_field(
            db,
            table.id,
            core.id,
            FieldUpdate(label="不能改", expected_schema_version=4),
            actor=ADMIN,
        )


def test_first_save_retry_revision_validation_and_history(data_db):
    db = data_db
    install_core_schema(db)
    table = system_table(db, PRODUCTS_ID, "products", "产品")
    product = Product(product_name_en="APPLE", code="P-1")
    db.add(product)
    db.commit()
    field = extension_field(db, table.id)
    request_id = uuid4()
    body = SystemRecordUpdate(
        request_id=request_id,
        source_record_id=str(product.id),
        expected_revision=0,
        schema_version=2,
        values={str(field.id): "首次核对"},
    )

    created = s.save_system_record(db, table.id, body, actor=ADMIN)
    assert created.id == str(product.id)
    assert created.revision == 1
    assert created.values[str(field.id)] == "首次核对"
    assert db.scalar(select(func.count()).select_from(DataRecord)) == 1
    assert db.scalar(select(func.count()).select_from(DataChange)) == 2

    retried = s.save_system_record(db, table.id, body, actor=ADMIN)
    assert retried.revision == 1
    assert db.scalar(select(func.count()).select_from(DataRecord)) == 1
    with pytest.raises(Conflict):
        s.save_system_record(
            db,
            table.id,
            body.model_copy(update={"values": {str(field.id): "不同内容"}}),
            actor=ADMIN,
        )
    with pytest.raises(Conflict):
        s.save_system_record(db, table.id, body, actor=EMPLOYEE)

    extension_field(db, table.id, kind="boolean")
    assert s.save_system_record(db, table.id, body, actor=ADMIN).revision == 1

    updated = s.save_system_record(
        db,
        table.id,
        SystemRecordUpdate(
            request_id=uuid4(),
            source_record_id=str(product.id),
            expected_revision=1,
            schema_version=3,
            values={str(field.id): "复核完成"},
        ),
        actor=ADMIN,
    )
    assert updated.revision == 2
    assert updated.values[str(field.id)] == "复核完成"
    with pytest.raises(Conflict):
        s.save_system_record(
            db,
            table.id,
            SystemRecordUpdate(
                request_id=uuid4(),
                source_record_id=str(product.id),
                expected_revision=1,
                schema_version=3,
                values={str(field.id): "过期覆盖"},
            ),
            actor=ADMIN,
        )


def test_order_visibility_and_deleted_source_hide_anchor_and_history(data_db):
    db = data_db
    install_core_schema(db)
    table = system_table(db, ORDERS_ID, "orders", "订单")
    own = Order(user_id=EMPLOYEE.id, filename="own.pdf", po_number="PO-OWN")
    other = Order(user_id=20, filename="other.pdf", po_number="PO-OTHER")
    db.add_all([own, other])
    db.commit()
    field = extension_field(db, table.id)

    with pytest.raises(NotFound):
        s.save_system_record(
            db,
            table.id,
            SystemRecordUpdate(
                request_id=uuid4(),
                source_record_id=str(other.id),
                expected_revision=0,
                schema_version=2,
                values={str(field.id): "越权"},
            ),
            actor=EMPLOYEE,
        )
    saved = s.save_system_record(
        db,
        table.id,
        SystemRecordUpdate(
            request_id=uuid4(),
            source_record_id=str(own.id),
            expected_revision=0,
            schema_version=2,
            values={str(field.id): "本人订单"},
        ),
        actor=EMPLOYEE,
    )
    assert saved.id == str(own.id)
    assert s.list_changes(db, table.id, ChangeQuery(), actor=EMPLOYEE).total == 2

    db.delete(own)
    db.commit()
    with pytest.raises(NotFound):
        s.save_system_record(
            db,
            table.id,
            SystemRecordUpdate(
                request_id=uuid4(),
                source_record_id=str(own.id),
                expected_revision=1,
                schema_version=2,
                values={str(field.id): "来源已经删除"},
            ),
            actor=EMPLOYEE,
        )
    assert s.list_records(db, table.id, RecordQuery(), actor=EMPLOYEE).total == 0
    with pytest.raises(NotFound):
        s.get_record(db, table.id, str(own.id), actor=EMPLOYEE)
    assert s.list_changes(db, table.id, ChangeQuery(), actor=EMPLOYEE).total == 1
    assert db.scalar(select(func.count()).select_from(DataRecord)) == 1
