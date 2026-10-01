"""Direct edit HTTP permissions and prepare/confirm boundary."""

from domains.masterdata.models import Product
from test_v2.fixtures.helpers import login, seed_product, seed_user


def _setup(client, factory, role="employee"):
    db = factory()
    email = f"direct-{role}@example.com"
    seed_user(db, email=email, role=role)
    product = seed_product(db, name="Direct Original")
    payload = {
        "selected_product_ids": [product.id],
        "scope": "basic",
        "operation": "edit",
        "rows": [
            {
                "product_id": product.id,
                "expected_revision": product.revision,
                "values": {"brand": "Brand A"},
            }
        ],
    }
    db.close()
    return login(client, email), payload


def test_direct_prepare_is_read_only_until_commit_and_shows_real_diff(client, session_factory):
    headers, payload = _setup(client, session_factory)
    response = client.post(
        "/api/data-upload/workbench/products/prepare-update", headers=headers, json=payload
    )
    assert response.status_code == 200, response.text
    batch = response.json()
    assert batch["can_continue"] is True
    db = session_factory()
    assert db.get(Product, payload["selected_product_ids"][0]).brand is None
    db.close()
    rows = client.get(
        f"/api/data-upload/workbench/batches/{batch['id']}/rows", headers=headers
    ).json()
    assert rows["items"][0]["source_row_number"] == 1
    assert rows["items"][0]["fields"][0]["after"] == "Brand A"
    result = client.post(
        f"/api/data-upload/workbench/batches/{batch['id']}/commit", headers=headers
    )
    assert result.status_code == 200, result.text
    assert result.json()["updated"] == 1


def test_direct_update_rejects_finance(client, session_factory):
    headers, payload = _setup(client, session_factory, "finance")
    response = client.post(
        "/api/data-upload/workbench/products/prepare-update", headers=headers, json=payload
    )
    assert response.status_code == 403


def test_direct_update_rejects_out_of_scope_product(client, session_factory):
    headers, payload = _setup(client, session_factory)
    payload["rows"][0]["product_id"] = 9999
    response = client.post(
        "/api/data-upload/workbench/products/prepare-update", headers=headers, json=payload
    )
    assert response.status_code == 422
    assert "不在本次选择" in response.json()["detail"]


def test_invalid_basic_patch_cannot_be_revalidated_to_bypass_error(client, session_factory):
    headers, payload = _setup(client, session_factory)
    payload["rows"][0]["values"] = {"product_name_en": ""}
    response = client.post(
        "/api/data-upload/workbench/products/prepare-update", headers=headers, json=payload
    )
    assert response.status_code == 200, response.text
    assert response.json()["can_continue"] is False
    batch_id = response.json()["id"]
    checked = client.post(
        f"/api/data-upload/workbench/batches/{batch_id}/validate", headers=headers
    )
    assert checked.status_code == 200
    assert checked.json()["can_continue"] is False
    committed = client.post(
        f"/api/data-upload/workbench/batches/{batch_id}/commit", headers=headers
    )
    assert committed.status_code == 409
