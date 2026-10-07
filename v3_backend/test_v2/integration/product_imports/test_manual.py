"""Manual preparation edits must use the same data rules and safe deletion."""

from datetime import date

from sqlalchemy import select

from domains.masterdata.models import Product, ProductPricePeriod
from test_v2.integration.product_imports.test_api import _login
from test_v2.integration.product_imports.test_validation import setup_data


def prepare(client, db, seed_user, monkeypatch):
    japan, osaka, _, supplier, category = setup_data(db)
    monkeypatch.setattr("apps.http.database_setup.settings.TEMP_DATABASE_SETUP_ENABLED", True)
    p = Product(
        code="MANUAL-1",
        product_name_en="APPLE",
        country_id=japan.id,
        port_id=osaka.id,
        supplier_id=supplier.id,
        category_id=category.id,
        status=True,
    )
    db.add(p)
    db.commit()
    return p, _login(client, seed_user)


def test_product_edit_uses_configured_fields_and_rejects_stale_revision(
    client, db, seed_user, monkeypatch
):
    p, h = prepare(client, db, seed_user, monkeypatch)
    config = client.get(f"/api/database-setup/products/{p.id}/edit-config", headers=h)
    assert config.status_code == 200
    body = {
        "expected_revision": p.revision,
        "schema_version": config.json()["schema_version"],
        "values": {"product_name": "CHANGED APPLE", "unit": "KG"},
    }
    saved = client.patch(f"/api/database-setup/products/{p.id}", headers=h, json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["name"] == "CHANGED APPLE"
    assert saved.json()["unit"] == "KG"
    assert (
        client.patch(f"/api/database-setup/products/{p.id}", headers=h, json=body).status_code
        == 409
    )
    body["expected_revision"] = saved.json()["revision"]
    body["values"] = {"unknown": "bad"}
    assert (
        client.patch(f"/api/database-setup/products/{p.id}", headers=h, json=body).status_code
        == 422
    )


def test_manual_price_create_update_overlap_and_delete(client, db, seed_user, monkeypatch):
    p, h = prepare(client, db, seed_user, monkeypatch)
    base = f"/api/database-setup/products/{p.id}"
    payload = {
        "price_type": "purchase",
        "amount": 100,
        "currency": "JPY",
        "effective_from": "2027-01-01",
        "effective_to": "2027-03-31",
    }
    created = client.post(base + "/periods", headers=h, json=payload)
    assert created.status_code == 201, created.text
    period = created.json()
    overlap = client.post(base + "/periods", headers=h, json={**payload, "amount": 120})
    assert overlap.status_code == 409
    updated = client.patch(
        base + f"/periods/{period['id']}",
        headers=h,
        json={
            "expected_revision": period["revision"],
            "amount": 135,
            "currency": "JPY",
            "effective_from": "2027-01-01",
            "effective_to": "2027-04-30",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["amount"] == 135
    stale = client.request(
        "DELETE",
        base + f"/periods/{period['id']}",
        headers=h,
        json={"expected_revision": period["revision"]},
    )
    assert stale.status_code == 409
    deleted = client.request(
        "DELETE",
        base + f"/periods/{period['id']}",
        headers=h,
        json={"expected_revision": updated.json()["revision"]},
    )
    assert deleted.status_code == 200, deleted.text
    assert db.get(ProductPricePeriod, period["id"]) is None


def test_product_permanent_delete_requires_code_and_current_revision(
    client, db, seed_user, monkeypatch
):
    p, h = prepare(client, db, seed_user, monkeypatch)
    db.add(
        ProductPricePeriod(
            product_id=p.id,
            price_type="purchase",
            amount=1,
            currency="JPY",
            effective_from=date(2027, 1, 1),
            effective_to=date(2027, 1, 31),
            status=True,
        )
    )
    db.commit()
    base = f"/api/database-setup/products/{p.id}"
    preview = client.get(base + "/deletion", headers=h)
    assert preview.status_code == 200
    assert preview.json()["period_count"] == 1
    assert preview.json()["can_delete"] is True
    body = {"expected_revision": p.revision, "confirm_code": "WRONG"}
    assert client.request("DELETE", base, headers=h, json=body).status_code == 409
    body["confirm_code"] = p.code
    deleted = client.request("DELETE", base, headers=h, json=body)
    assert deleted.status_code == 200, deleted.text
    assert db.scalar(select(Product.id).where(Product.id == p.id)) is None
    assert (
        db.scalar(select(ProductPricePeriod.id).where(ProductPricePeriod.product_id == p.id))
        is None
    )
    assert client.get(base, headers=h).status_code == 404


def test_product_delete_blocks_associated_images(client, db, seed_user, monkeypatch):
    from domains.masterdata.models import ProductImage

    p, h = prepare(client, db, seed_user, monkeypatch)
    db.add(
        ProductImage(
            product_id=p.id,
            storage_key="x",
            thumbnail_key="x",
            medium_key="x",
            filename="x.jpg",
            file_type="image/jpeg",
            file_size_bytes=1,
            uploaded_by_user_id=seed_user.id,
        )
    )
    db.commit()
    base = f"/api/database-setup/products/{p.id}"
    preview = client.get(base + "/deletion", headers=h)
    assert preview.json()["can_delete"] is False
    assert (
        client.request(
            "DELETE",
            base,
            headers=h,
            json={"expected_revision": p.revision, "confirm_code": p.code},
        ).status_code
        == 409
    )
    assert client.get(base, headers=h).status_code == 200


def test_edit_required_field_and_schema_change_do_not_write(client, db, seed_user, monkeypatch):
    p, h = prepare(client, db, seed_user, monkeypatch)
    base = f"/api/database-setup/products/{p.id}"
    config = client.get(base + "/edit-config", headers=h).json()
    body = {
        "expected_revision": p.revision,
        "schema_version": config["schema_version"],
        "values": {"product_name": ""},
    }
    assert client.patch(base, headers=h, json=body).status_code == 422
    body["schema_version"] += 1
    assert client.patch(base, headers=h, json=body).status_code == 409
    assert client.get(base, headers=h).json()["name"] == "APPLE"


def test_manual_extension_unique_value_cannot_be_used_by_another_product(
    client, db, seed_user, monkeypatch
):
    from uuid import uuid4

    from domains.dynamic_data import PRODUCT_TABLE_ID
    from domains.dynamic_data.models import DataField

    p, h = prepare(client, db, seed_user, monkeypatch)
    other = Product(
        code="MANUAL-2",
        product_name_en="OTHER",
        country_id=p.country_id,
        port_id=p.port_id,
        status=True,
    )
    field = DataField(
        id=uuid4(),
        table_id=PRODUCT_TABLE_ID,
        label="唯一测试",
        field_type="text",
        unique=True,
        required=False,
        config={},
        status="active",
        schema_version=2,
        sort_order=1,
    )
    db.add_all([other, field])
    db.commit()
    for target, expected in [(p, 200), (other, 409)]:
        base = f"/api/database-setup/products/{target.id}"
        config = client.get(base + "/edit-config", headers=h).json()
        body = {
            "expected_revision": config["expected_revision"],
            "schema_version": config["schema_version"],
            "extension_revision": config["extension_revision"],
            "values": {f"extension:{field.id}": "SAME"},
        }
        response = client.patch(base, headers=h, json=body)
        assert response.status_code == expected, response.text


def test_manual_product_can_be_disabled_and_reenabled_by_id(client, db, seed_user, monkeypatch):
    p, h = prepare(client, db, seed_user, monkeypatch)
    base = f"/api/database-setup/products/{p.id}"
    for state in ["停用", "启用"]:
        config = client.get(base + "/edit-config", headers=h).json()
        response = client.patch(
            base,
            headers=h,
            json={
                "expected_revision": config["expected_revision"],
                "schema_version": config["schema_version"],
                "extension_revision": config["extension_revision"],
                "values": {"status": state},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] is (state == "启用")


def test_deletion_is_blocked_after_business_use(client, db, seed_user, monkeypatch):
    from domains.orders.models import Order

    p, h = prepare(client, db, seed_user, monkeypatch)
    db.add(Order(user_id=seed_user.id, filename="business.pdf", status="completed"))
    db.commit()
    base = f"/api/database-setup/products/{p.id}"
    preview = client.get(base + "/deletion", headers=h).json()
    assert not preview["can_delete"]
    assert "订单或询价" in preview["reasons"][0]
    response = client.request(
        "DELETE", base, headers=h, json={"expected_revision": p.revision, "confirm_code": p.code}
    )
    assert response.status_code == 409
    assert client.get(base, headers=h).status_code == 200


def test_manual_routes_require_login_and_preparation_enablement(client, db, seed_user, monkeypatch):
    p, h = prepare(client, db, seed_user, monkeypatch)
    base = f"/api/database-setup/products/{p.id}"
    assert client.get(base + "/edit-config").status_code == 401
    monkeypatch.setattr("apps.http.database_setup.settings.TEMP_DATABASE_SETUP_ENABLED", False)
    assert client.get(base + "/edit-config", headers=h).status_code == 503
    assert client.post(base + "/periods", headers=h, json={}).status_code == 503
    assert client.request("DELETE", base, headers=h, json={}).status_code == 503
