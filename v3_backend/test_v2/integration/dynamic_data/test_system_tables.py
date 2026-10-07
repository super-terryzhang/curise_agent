"""System tables expose core rows without copying or widening visibility."""

from datetime import UTC, datetime
from importlib import import_module
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select

from domains.document.models import Document, DocumentFolder
from domains.dynamic_data import service as s
from domains.dynamic_data.errors import NotFound, ValidationError
from domains.dynamic_data.models import DataField, DataRecord, DataTable
from domains.dynamic_data.schemas import Actor, RecordFilter, RecordQuery
from domains.identity.models import User
from domains.masterdata.models import Category, Country, Port, Product, Supplier
from domains.orders.models import Order, OrderGroup
from infrastructure.db.base import Base

SYSTEM_TABLE_IDS = {
    "products": UUID("025588dd-ae63-5607-9e78-1179a500ed6e"),
    "suppliers": UUID("480cc5e5-5882-58d1-a227-e8ee734c866f"),
    "orders": UUID("0bc67ecc-ccd3-53b5-8327-74f4b482b7b4"),
}
ADMIN = Actor(id=1, role="admin")
EMPLOYEE = Actor(id=10, role="employee")


def registry():
    return import_module("domains.dynamic_data.system_tables")


def install_core_schema(db):
    # Loading referenced models makes SQLAlchemy resolve all FK targets; the
    # rows below keep optional order references NULL.
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


def seed_catalog(db):
    now = datetime(2026, 10, 6, tzinfo=UTC)
    names = {"products": "产品", "suppliers": "供应商", "orders": "订单"}
    db.add_all(
        [
            DataTable(
                id=table_id,
                name=names[key],
                table_kind="system",
                system_key=key,
                created_by=0,
                updated_by=0,
                created_at=now,
                updated_at=now,
            )
            for key, table_id in SYSTEM_TABLE_IDS.items()
        ]
    )
    db.commit()


def seed_relations(db):
    country = Country(name="日本", code="JP")
    category = Category(name="食品", code="FOOD")
    port = Port(name="大阪", code="OSA")
    supplier = Supplier(name="大阪食品株式会社", contact="田中", email="sales@example.jp")
    db.add_all([country, category, port, supplier])
    db.flush()
    supplier.country_id = country.id
    port.country_id = country.id
    return country, category, port, supplier


def test_catalog_lists_system_and_user_tables_with_actor_scoped_counts(data_db):
    db = data_db
    install_core_schema(db)
    seed_catalog(db)
    country, category, port, supplier = seed_relations(db)
    db.add_all(
        [
            Product(
                product_name_en="APPLE",
                code="P-1",
                country_id=country.id,
                category_id=category.id,
                port_id=port.id,
                supplier_id=supplier.id,
            ),
            Product(product_name_en="BANANA", code="P-2"),
            Order(user_id=EMPLOYEE.id, filename="mine.pdf", po_number="PO-MINE"),
            Order(user_id=20, filename="other.pdf", po_number="PO-OTHER"),
            DataTable(id=uuid4(), name="检查表", created_by=1, updated_by=1),
            DataTable(
                id=uuid4(),
                name="旧检查表",
                status="archived",
                created_by=1,
                updated_by=1,
            ),
        ]
    )
    db.commit()

    active = s.list_tables(db, actor=EMPLOYEE, status="active", page=1, page_size=50)
    assert {item.name for item in active.items} == {"产品", "供应商", "订单", "检查表"}
    counts = {item.system_key: item.record_count for item in active.items if item.system_key}
    assert counts == {"products": 2, "suppliers": 1, "orders": 1}
    assert all(item.table_kind == "system" for item in active.items if item.system_key)

    archived = s.list_tables(db, actor=EMPLOYEE, status="archived", page=1, page_size=50)
    assert [item.name for item in archived.items] == ["旧检查表"]


def test_product_supplier_order_adapters_read_declared_fields_without_writes(data_db):
    db = data_db
    install_core_schema(db)
    seed_catalog(db)
    country, category, port, supplier = seed_relations(db)
    product = Product(
        product_name_en="APPLE FUJI",
        product_name_jp="ふじりんご",
        code="P-APPLE",
        country_id=country.id,
        category_id=category.id,
        port_id=port.id,
        supplier_id=supplier.id,
        unit="KG",
        brand="TEST BRAND",
    )
    order = Order(
        user_id=EMPLOYEE.id,
        filename="po.pdf",
        po_number="PO-READ",
        ship_name="MILLENNIUM",
        loading_date="2026-10-20",
        destination_port="OSAKA",
        status="completed",
    )
    db.add_all([product, order])
    db.commit()

    product_fields = s.list_fields(db, SYSTEM_TABLE_IDS["products"], include_archived=True)
    product_keys = {field.system_key: field for field in product_fields if field.source == "core"}
    assert {
        "product_name_en",
        "product_name_jp",
        "code",
        "supplier",
        "country",
        "category",
        "port",
        "unit",
        "brand",
        "status",
    } == set(product_keys)
    assert all(field.locked for field in product_keys.values())

    page = s.list_records(
        db,
        SYSTEM_TABLE_IDS["products"],
        RecordQuery(q="APPLE", page_size=50),
        actor=EMPLOYEE,
    )
    assert page.total == 1
    assert page.items[0].id == str(product.id)
    assert page.items[0].values[str(product_keys["supplier"].id)] == "大阪食品株式会社"
    assert page.items[0].values[str(product_keys["port"].id)] == "大阪"
    assert page.items[0].business_url == f"/dashboard/data/products/{product.id}"
    assert page.items[0].revision == 0
    assert page.items[0].created_by is None

    supplier_fields = {
        field.system_key: field
        for field in s.list_fields(
            db, SYSTEM_TABLE_IDS["suppliers"], include_archived=True
        )
        if field.source == "core"
    }
    supplier_row = s.get_record(
        db, SYSTEM_TABLE_IDS["suppliers"], str(supplier.id), actor=EMPLOYEE
    )
    assert supplier_row.values[str(supplier_fields["country"].id)] == "日本"
    assert supplier_row.business_url == f"/dashboard/data?tab=suppliers&edit={supplier.id}"

    order_fields = {
        field.system_key: field
        for field in s.list_fields(db, SYSTEM_TABLE_IDS["orders"], include_archived=True)
        if field.source == "core"
    }
    order_row = s.get_record(db, SYSTEM_TABLE_IDS["orders"], str(order.id), actor=EMPLOYEE)
    assert order_row.values[str(order_fields["po_number"].id)] == "PO-READ"
    assert order_row.business_url == f"/dashboard/orders/{order.id}"
    assert db.scalar(select(func.count()).select_from(DataRecord)) == 0


def test_system_page_batches_relation_labels_and_extension_values(data_db):
    db = data_db
    install_core_schema(db)
    seed_catalog(db)
    country, category, port, supplier = seed_relations(db)
    products = [
        Product(
            product_name_en=f"BATCH PRODUCT {index:02d}",
            code=f"P-{index:02d}",
            country_id=country.id,
            category_id=category.id,
            port_id=port.id,
            supplier_id=supplier.id,
        )
        for index in range(50)
    ]
    db.add_all(products)
    db.flush()
    extension = DataField(
        id=uuid4(),
        table_id=SYSTEM_TABLE_IDS["products"],
        label="内部备注",
        field_type="text",
        sort_order=0,
    )
    db.add(extension)
    db.flush()
    db.add(
        DataRecord(
            id=uuid4(),
            table_id=SYSTEM_TABLE_IDS["products"],
            source_record_id=str(products[0].id),
            values={str(extension.id): "已核对"},
            created_by=1,
            updated_by=1,
        )
    )
    db.commit()
    statements = []

    def count(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", count)
    try:
        page = s.list_records(
            db,
            SYSTEM_TABLE_IDS["products"],
            RecordQuery(page_size=50),
            actor=ADMIN,
        )
    finally:
        event.remove(db.bind, "before_cursor_execute", count)

    fields = {field.system_key: field for field in s.list_fields(
        db, SYSTEM_TABLE_IDS["products"], include_archived=True
    ) if field.source == "core"}
    assert page.total == 50
    assert len(page.items) == 50
    assert {row.values[str(fields["supplier"].id)] for row in page.items} == {
        "大阪食品株式会社"
    }
    assert any(row.values.get(str(extension.id)) == "已核对" for row in page.items)
    assert len(statements) <= 10


def test_order_adapter_reuses_actor_visibility_for_list_and_detail(data_db):
    db = data_db
    install_core_schema(db)
    seed_catalog(db)
    own = Order(user_id=EMPLOYEE.id, filename="own.pdf", po_number="PO-OWN")
    other = Order(user_id=20, filename="other.pdf", po_number="PO-OTHER")
    db.add_all([own, other])
    db.commit()

    employee_page = s.list_records(
        db, SYSTEM_TABLE_IDS["orders"], RecordQuery(), actor=EMPLOYEE
    )
    assert [row.id for row in employee_page.items] == [str(own.id)]
    assert s.get_record(
        db, SYSTEM_TABLE_IDS["orders"], str(own.id), actor=EMPLOYEE
    ).id == str(own.id)
    with pytest.raises(NotFound):
        s.get_record(db, SYSTEM_TABLE_IDS["orders"], str(other.id), actor=EMPLOYEE)

    admin_page = s.list_records(db, SYSTEM_TABLE_IDS["orders"], RecordQuery(), actor=ADMIN)
    assert {row.id for row in admin_page.items} == {str(own.id), str(other.id)}

    with pytest.raises(ValidationError):
        s.list_records(
            db,
            SYSTEM_TABLE_IDS["orders"],
            RecordQuery(
                filters=[RecordFilter(field_id=uuid4(), operator="eq", value="PO-OWN")]
            ),
            actor=EMPLOYEE,
        )
    assert (
        s.list_records(
            db,
            SYSTEM_TABLE_IDS["orders"],
            RecordQuery(q="%' OR 1=1 --"),
            actor=ADMIN,
        ).total
        == 0
    )
