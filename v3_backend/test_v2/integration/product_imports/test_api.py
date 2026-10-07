"""Temporary database-setup HTTP flow and authorization."""

from datetime import date

from test_v2.integration.product_imports.test_validation import (
    build_upload,
    price,
    product,
    setup_data,
)


def _login(client, seed_user):
    response = client.post(
        "/api/auth/login",
        json={"email": seed_user.email, "password": "password123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_disabled_gate_returns_503(client, monkeypatch):
    monkeypatch.setattr("apps.http.database_setup.settings.TEMP_DATABASE_SETUP_ENABLED", False)
    response = client.get("/api/database-setup/status")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "MODULE_DISABLED"


def test_enabled_gate_rejects_non_admin(client, auth_tokens, monkeypatch):
    monkeypatch.setattr("apps.http.database_setup.settings.TEMP_DATABASE_SETUP_ENABLED", True)
    response = client.get(
        "/api/database-setup/status",
        headers={"Authorization": f"Bearer {auth_tokens['access_token']}"},
    )
    assert response.status_code == 403


def test_complete_api_flow_and_commit_retry(
    client, db, seed_user, monkeypatch
):
    setup_data(db)
    monkeypatch.setattr("apps.http.database_setup.settings.TEMP_DATABASE_SETUP_ENABLED", True)
    headers = _login(client, seed_user)
    setup_status = client.get("/api/database-setup/status", headers=headers)
    assert setup_status.status_code == 200
    assert setup_status.json()["database_name"] == "cruise_v3_clean"
    assert setup_status.json()["schema_version"] >= 1
    cannot_switch = client.get(
        "/api/database-setup/status?database=production", headers=headers
    )
    assert cannot_switch.json()["database_name"] == "cruise_v3_clean"
    blob = build_upload(
        db,
        [product("API-1", 业务分类="中标产品")],
        [price("API-1", date(2027, 5, 1), date(2027, 5, 31), amount=88)],
    )

    uploaded = client.post(
        "/api/database-setup/imports",
        headers=headers,
        files={"file": ("products.xlsx", blob, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert uploaded.status_code == 201, uploaded.text
    batch_id = uploaded.json()["id"]

    checked = client.post(
        f"/api/database-setup/imports/{batch_id}/validate", headers=headers
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "ready"
    rows = client.get(
        f"/api/database-setup/imports/{batch_id}/rows?page=1&page_size=20",
        headers=headers,
    )
    assert rows.status_code == 200
    assert rows.json()["can_commit"] is True
    assert rows.json()["total"] == 2
    product_row = next(item for item in rows.json()["items"] if item["sheet"] == "products")
    assert product_row["normalized_values"]["extensions"][0]["label"] == "业务分类"
    assert "extension_values" not in product_row["normalized_values"]

    first = client.post(
        f"/api/database-setup/imports/{batch_id}/commit", headers=headers
    )
    retry = client.post(
        f"/api/database-setup/imports/{batch_id}/commit", headers=headers
    )
    assert first.status_code == retry.status_code == 200
    assert first.json() == retry.json()

    listing = client.get("/api/database-setup/products?q=API-1", headers=headers)
    assert listing.status_code == 200
    product_id = listing.json()["items"][0]["id"]
    detail = client.get(
        f"/api/database-setup/products/{product_id}", headers=headers
    )
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["code"] == "API-1"
    assert payload["price_periods"][0]["amount"] == 88.0
    assert payload["extensions"][0]["label"] == "业务分类"
    assert "field_id" not in payload["extensions"][0]


def test_schema_changed_after_upload_remains_readable_but_not_committable(
    client, db, seed_user, monkeypatch
):
    setup_data(db)
    monkeypatch.setattr("apps.http.database_setup.settings.TEMP_DATABASE_SETUP_ENABLED", True)
    headers = _login(client, seed_user)
    blob = build_upload(db, [product("STALE-1", 业务分类="临时产品")])
    uploaded = client.post(
        "/api/database-setup/imports",
        headers=headers,
        files={"file": ("stale.xlsx", blob)},
    )
    batch_id = uploaded.json()["id"]
    from domains.dynamic_data.models import DataTable
    from scripts.seed_clean_product_fields import PRODUCT_TABLE_ID

    product_table = db.get(DataTable, PRODUCT_TABLE_ID)
    product_table.schema_version += 1
    db.commit()

    checked = client.post(
        f"/api/database-setup/imports/{batch_id}/validate", headers=headers
    )
    assert checked.status_code == 200
    assert checked.json()["status"] == "failed"
    rows = client.get(
        f"/api/database-setup/imports/{batch_id}/rows", headers=headers
    )
    assert rows.status_code == 200
    assert rows.json()["can_commit"] is False
    assert any(issue["code"] == "STALE_SCHEMA" for issue in rows.json()["issues"])
