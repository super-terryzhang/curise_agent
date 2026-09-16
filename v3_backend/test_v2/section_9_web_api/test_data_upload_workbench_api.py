"""HTTP contract for the deterministic workbench product uploader."""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from domains.masterdata.upload.models import UploadBatch
from test_v2.fixtures.helpers import login, make_excel, seed_default_location, seed_user


def _headers(client: TestClient, session_factory, *, role: str = "employee") -> dict[str, str]:
    email = f"workbench-{role}@example.com"
    db = session_factory()
    try:
        seed_user(db, email=email, role=role)
        seed_default_location(db)
    finally:
        db.close()
    return login(client, email)


def _post(client, headers, blob, filename="products.xlsx"):
    return client.post(
        "/api/data-upload/workbench/products/upload",
        files={"file": (filename, blob, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )


def test_workbench_upload_persists_unique_original_and_sha256(
    client, session_factory, _local_storage
):
    headers = _headers(client, session_factory)
    blob = make_excel([{"product_name": "Apple", "price": 10}])

    first = _post(client, headers, blob)
    second = _post(client, headers, blob)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    db = session_factory()
    try:
        batches = db.query(UploadBatch).order_by(UploadBatch.id).all()
        assert batches[-2].file_url != batches[-1].file_url
        assert batches[-1].file_sha256 == hashlib.sha256(blob).hexdigest()
        assert _local_storage.download(batches[-1].file_url) == blob
        assert batches[-1].workflow_version == 2
    finally:
        db.close()


def test_workbench_upload_only_accepts_xlsx(client, session_factory):
    headers = _headers(client, session_factory)
    response = _post(client, headers, b"name\nApple", filename="products.csv")
    assert response.status_code == 400
    assert ".xlsx" in response.json()["detail"]


def test_finance_cannot_upload_product_masterdata(client, session_factory):
    headers = _headers(client, session_factory, role="finance")
    response = _post(client, headers, make_excel([{"product_name": "Apple"}]))
    assert response.status_code == 403


def test_validate_rows_and_commit_are_direct_http_steps(client, session_factory):
    headers = _headers(client, session_factory)
    uploaded = _post(client, headers, make_excel([{"product_name": "Apple", "price": 10}]))
    assert uploaded.status_code == 200, uploaded.text
    batch_id = uploaded.json()["id"]

    checked = client.post(
        f"/api/data-upload/workbench/batches/{batch_id}/validate", headers=headers
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["can_continue"] is True

    rows = client.get(
        f"/api/data-upload/workbench/batches/{batch_id}/rows?view=changes&page=1&page_size=20",
        headers=headers,
    )
    assert rows.status_code == 200, rows.text
    assert rows.json()["items"][0]["source_row_number"] == 2

    committed = client.post(
        f"/api/data-upload/workbench/batches/{batch_id}/commit", headers=headers
    )
    assert committed.status_code == 200, committed.text
    assert committed.json()["created"] == 1

    history = client.get(
        "/api/data-upload/workbench/batches?page=1&page_size=10", headers=headers
    )
    assert history.status_code == 200, history.text
    assert history.json()["items"][0]["status"] == "completed"
    assert history.json()["items"][0]["summary"]["create"] == 1

    original = client.get(
        f"/api/data-upload/workbench/batches/{batch_id}/original-url", headers=headers
    )
    assert original.status_code == 200, original.text
    assert "expires=" in original.json()["url"]
    assert "signature=" in original.json()["url"]

    rolled_back = client.post(
        f"/api/data-upload/workbench/batches/{batch_id}/rollback", headers=headers
    )
    assert rolled_back.status_code == 200, rolled_back.text
    assert rolled_back.json()["deleted"] == 1


def test_commit_endpoint_rejects_known_validation_errors(client, session_factory):
    headers = _headers(client, session_factory)
    blob = make_excel([{"product_name": "Apple", "unexpected": "value"}])
    uploaded = _post(client, headers, blob)
    batch_id = uploaded.json()["id"]
    client.post(f"/api/data-upload/workbench/batches/{batch_id}/validate", headers=headers)

    committed = client.post(
        f"/api/data-upload/workbench/batches/{batch_id}/commit", headers=headers
    )
    assert committed.status_code == 409
