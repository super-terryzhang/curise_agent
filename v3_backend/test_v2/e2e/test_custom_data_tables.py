"""Public authenticated workflow; fixed literal expectations, no matcher side effects."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from domains.masterdata.models import Product, ProductPricePeriod
from domains.orders.models import Order
from infrastructure.config import settings


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
