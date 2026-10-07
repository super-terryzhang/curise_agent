"""Public authenticated workflow; fixed literal expectations, no matcher side effects."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select

from domains.dynamic_data.models import DataTable
from domains.identity.models import User
from domains.masterdata.models import Product, ProductPricePeriod, Supplier
from domains.orders.models import Order
from infrastructure.config import settings
from infrastructure.security import hash_password

SYSTEM_TABLE_IDS = {
    "products": UUID("025588dd-ae63-5607-9e78-1179a500ed6e"),
    "suppliers": UUID("480cc5e5-5882-58d1-a227-e8ee734c866f"),
    "orders": UUID("0bc67ecc-ccd3-53b5-8327-74f4b482b7b4"),
}


def test_custom_table_complete_user_workflow_preserves_business(client, db, seed_user, monkeypatch):
    monkeypatch.setattr(settings, "CUSTOM_DATA_TABLES_ENABLED", True)
    p = Product(
        product_name_en="既有产品",
        code="SAME-CODE",
        price=Decimal("10.00"),
        contract_price=Decimal("20.00"),
    )
    db.add(p)
    db.flush()
    db.add(
        ProductPricePeriod(
            product_id=p.id,
            price_type="purchase",
            amount=Decimal("10.00"),
            currency="JPY",
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 3, 31),
        )
    )
    db.add(
        Order(
            user_id=seed_user.id,
            filename="baseline.pdf",
            po_number="BASELINE-PO",
            products=[{"code": "SAME-CODE", "quantity": 2}],
            match_results=[{"product_id": p.id, "matched": True}],
            status="ready",
        )
    )
    db.commit()

    def business():
        db.expire_all()
        return {
            m.__tablename__: [dict(r) for r in db.execute(select(m.__table__)).mappings()]
            for m in (Product, ProductPricePeriod, Order)
        }

    before = business()

    def login():
        response = client.post(
            "/api/auth/login", json={"email": seed_user.email, "password": "password123"}
        )
        assert response.status_code == 200
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    headers = login()

    def call(method, path, body=None, expected=200):
        response = client.request(method, "/api/data-tables" + path, headers=headers, json=body)
        assert response.status_code == expected, response.text
        return response.json()

    def table(name):
        return call("POST", "", {"id": str(uuid4()), "name": name})

    target = table("客户")
    target_name = str(uuid4())
    call(
        "POST",
        f"/{target['id']}/fields",
        {"id": target_name, "label": "名称", "field_type": "text", "expected_schema_version": 1},
    )
    call(
        "PATCH", f"/{target['id']}", {"display_field_id": target_name, "expected_schema_version": 2}
    )
    target_row = call(
        "POST",
        f"/{target['id']}/records",
        {"id": str(uuid4()), "schema_version": 3, "values": {target_name: "验收客户"}},
    )
    source = table("检验记录")
    keys = {}
    option = str(uuid4())
    for version, kind in enumerate(
        ["text", "number", "date", "datetime", "single_select", "multi_select", "boolean", "link"],
        1,
    ):
        key = str(uuid4())
        keys[kind] = key
        extra = (
            {"target_table_id": target["id"]}
            if kind == "link"
            else {"config": {"options": [{"id": option, "label": "通过", "active": True}]}}
            if "select" in kind
            else {}
        )
        call(
            "POST",
            f"/{source['id']}/fields",
            {
                "id": key,
                "label": kind,
                "field_type": kind,
                "expected_schema_version": version,
                **extra,
            },
        )
    source_id = source["id"]
    values = {
        keys["text"]: "原名称",
        keys["number"]: "0",
        keys["date"]: "2026-10-06",
        keys["datetime"]: "2026-10-06T09:00:00+09:00",
        keys["single_select"]: option,
        keys["multi_select"]: [option],
        keys["boolean"]: False,
        keys["link"]: target_row["id"],
    }
    row = call(
        "POST", f"/{source_id}/records", {"id": str(uuid4()), "schema_version": 9, "values": values}
    )
    assert row["values"][keys["number"]] == "0" and row["values"][keys["boolean"]] is False
    assert row["values"][keys["datetime"]] == "2026-10-06T00:00:00Z"
    assert row["linked_labels"][keys["link"]]["display_label"] == "验收客户"
    call(
        "PATCH",
        f"/{source_id}/records/{row['id']}",
        {"schema_version": 9, "expected_revision": 1, "values": {keys["number"]: "2.5"}},
    )
    history = call("GET", f"/{source_id}/changes?entity_type=record")["items"]
    change = next(h for h in history if h["action"] == "update_record")
    assert change["before"]["values"][keys["number"]] == "0"
    assert change["after"]["values"][keys["number"]] == "2.5"
    call(
        "PATCH",
        f"/{source_id}/fields/{keys['number']}",
        {"expected_schema_version": 9, "label": "数量"},
    )
    preserved = call("GET", f"/{source_id}/changes?entity_type=record")["items"]
    assert (
        next(h for h in preserved if h["id"] == change["id"])["display_snapshot"]["fields"][
            keys["number"]
        ]["label"]
        == "number"
    )
    invalid = call(
        "PATCH",
        f"/{source_id}/records/{row['id']}",
        {"schema_version": 10, "expected_revision": 2, "values": {keys["number"]: "非法"}},
        422,
    )
    assert invalid["detail"]["issues"][0]["field_id"] == keys["number"]
    assert len(call("GET", f"/{source_id}/changes?entity_type=record")["items"]) == 2
    call(
        "POST",
        f"/{target['id']}/records/{target_row['id']}/archive",
        {"schema_version": 3, "expected_revision": 1},
        422,
    )
    call(
        "PATCH",
        f"/{source_id}/records/{row['id']}",
        {"schema_version": 10, "expected_revision": 2, "values": {keys["link"]: None}},
    )
    for action, revision in [("archive", 3), ("restore", 4)]:
        call(
            "POST",
            f"/{source_id}/records/{row['id']}/{action}",
            {"schema_version": 10, "expected_revision": revision},
        )
    for action, revision in [("archive", 1), ("restore", 2)]:
        call(
            "POST",
            f"/{target['id']}/records/{target_row['id']}/{action}",
            {"schema_version": 3, "expected_revision": revision},
        )
    headers = login()
    fetched = call("GET", f"/{source_id}/records/{row['id']}")
    assert fetched["id"] == row["id"] and fetched["values"][keys["number"]] == "2.5"
    assert fetched["revision"] == 5 and fetched["status"] == "active"
    assert business() == before


def test_unified_system_tables_extend_core_rows_without_changing_business_data(
    client, db, seed_user, monkeypatch
):
    monkeypatch.setattr(settings, "CUSTOM_DATA_TABLES_ENABLED", True)
    employee = User(
        email="unified-employee@example.test",
        hashed_password=hash_password("password123"),
        full_name="Unified Employee",
        role="employee",
        is_active=True,
    )
    db.add(employee)
    db.flush()
    supplier = Supplier(name="验收供应商", email="supplier@example.test", status=True)
    db.add(supplier)
    db.flush()
    product = Product(
        product_name_en="Unified Product",
        code="UNIFIED-001",
        supplier_id=supplier.id,
        price=Decimal("10.00"),
        contract_price=Decimal("20.00"),
    )
    own_order = Order(
        user_id=employee.id,
        filename="own.pdf",
        po_number="UNIFIED-OWN",
        products=[],
        match_results=[],
        status="ready",
    )
    foreign_order = Order(
        user_id=seed_user.id,
        filename="foreign.pdf",
        po_number="UNIFIED-FOREIGN",
        products=[],
        match_results=[],
        status="ready",
    )
    db.add_all([product, own_order, foreign_order])
    for key, name in (("products", "产品"), ("suppliers", "供应商"), ("orders", "订单")):
        db.add(
            DataTable(
                id=SYSTEM_TABLE_IDS[key],
                name=name,
                description=f"{name}核心数据与扩展字段",
                table_kind="system",
                system_key=key,
                status="active",
                schema_version=1,
                created_by=0,
                updated_by=0,
            )
        )
    db.commit()

    def core_rows():
        db.expire_all()
        return {
            model.__tablename__: [
                dict(row) for row in db.execute(select(model.__table__)).mappings()
            ]
            for model in (Product, Supplier, Order)
        }

    before = core_rows()

    def login(email):
        response = client.post("/api/auth/login", json={"email": email, "password": "password123"})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def call(headers, method, path, body=None, expected=200):
        response = client.request(method, "/api/data-tables" + path, headers=headers, json=body)
        assert response.status_code == expected, response.text
        return response.json()

    admin_headers = login(seed_user.email)
    sources = {
        "products": str(product.id),
        "suppliers": str(supplier.id),
        "orders": str(foreign_order.id),
    }
    expected_core = {
        "products": "Unified Product",
        "suppliers": "验收供应商",
        "orders": "UNIFIED-FOREIGN",
    }
    for key, source_id in sources.items():
        table_id = str(SYSTEM_TABLE_IDS[key])
        extension_id = str(uuid4())
        field = call(
            admin_headers,
            "POST",
            f"/{table_id}/fields",
            {
                "id": extension_id,
                "label": "内部备注",
                "field_type": "text",
                "expected_schema_version": 1,
            },
        )
        assert field["source"] == "extension" and field["locked"] is False
        saved = call(
            admin_headers,
            "PATCH",
            f"/{table_id}/records/{source_id}",
            {
                "request_id": str(uuid4()),
                "source_record_id": source_id,
                "expected_revision": 0,
                "schema_version": 2,
                "values": {extension_id: f"{key}-checked"},
            },
        )
        assert saved["id"] == source_id
        assert saved["revision"] == 1
        assert saved["values"][extension_id] == f"{key}-checked"
        assert expected_core[key] in saved["display_label"]
        refreshed = call(admin_headers, "GET", f"/{table_id}/records/{source_id}")
        assert refreshed["values"][extension_id] == f"{key}-checked"
        history = call(
            admin_headers,
            "GET",
            f"/{table_id}/changes?entity_type=record&record_id={source_id}",
        )
        assert history["total"] == 1
        assert history["items"][0]["record_id"] == source_id

    employee_headers = login(employee.email)
    orders_id = str(SYSTEM_TABLE_IDS["orders"])
    employee_page = call(employee_headers, "GET", f"/{orders_id}/records")
    assert [row["id"] for row in employee_page["items"]] == [str(own_order.id)]
    call(
        employee_headers,
        "GET",
        f"/{orders_id}/records/{foreign_order.id}",
        expected=404,
    )
    hidden_history = call(
        employee_headers,
        "GET",
        f"/{orders_id}/changes?entity_type=record&record_id={foreign_order.id}",
    )
    assert hidden_history["total"] == 0
    call(
        employee_headers,
        "PATCH",
        f"/{orders_id}/records/{foreign_order.id}",
        {
            "request_id": str(uuid4()),
            "source_record_id": str(foreign_order.id),
            "expected_revision": 1,
            "schema_version": 2,
            "values": {},
        },
        expected=404,
    )
    assert core_rows() == before
