from uuid import uuid4

import pytest
from sqlalchemy import func, select

from domains.dynamic_data.models import DataChange


@pytest.fixture
def headers(client, seed_user):
    response = client.post(
        "/api/auth/login", json={"email": seed_user.email, "password": "password123"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_table(client, headers):
    response = client.post(
        "/api/data-tables", headers=headers, json={"id": str(uuid4()), "name": "接口表"}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    "role,read_allowed,structure_allowed",
    [
        ("superadmin", True, True),
        ("admin", True, True),
        ("employee", True, False),
        ("finance", True, False),
        ("viewer", False, False),
    ],
)
def test_roles_and_direct_url_enforce_structure_and_record_permissions(
    client, headers, db, seed_user, role, read_allowed, structure_allowed
):
    t = create_table(client, headers)
    field = client.post(
        f"/api/data-tables/{t['id']}/fields",
        headers=headers,
        json={
            "id": str(uuid4()),
            "label": "备注",
            "field_type": "text",
            "expected_schema_version": 1,
        },
    )
    assert field.status_code == 200
    seed_user.role = role
    db.commit()
    read = client.get("/api/data-tables", headers=headers)
    assert read.status_code == (200 if read_allowed else 403)
    structure = client.post(
        "/api/data-tables", headers=headers, json={"id": str(uuid4()), "name": "权限"}
    )
    assert structure.status_code == (200 if structure_allowed else 403)
    record = client.post(
        f"/api/data-tables/{t['id']}/records",
        headers=headers,
        json={"id": str(uuid4()), "schema_version": 2, "values": {}},
    )
    assert record.status_code == (200 if read_allowed else 403)


def test_validation_returns_chinese_field_issues(client, headers, db):
    t = create_table(client, headers)
    field_id = str(uuid4())
    client.post(
        f"/api/data-tables/{t['id']}/fields",
        headers=headers,
        json={
            "id": field_id,
            "label": "数量",
            "field_type": "number",
            "expected_schema_version": 1,
        },
    )
    before = db.scalar(select(func.count()).select_from(DataChange))
    response = client.post(
        f"/api/data-tables/{t['id']}/records",
        headers=headers,
        json={"id": str(uuid4()), "schema_version": 2, "values": {field_id: "错误数字"}},
    )
    assert response.status_code == 422, response.text
    issue = response.json()["detail"]["issues"][0]
    assert issue["field_id"] == field_id
    assert "数字" in issue["message"]
    assert db.scalar(select(func.count()).select_from(DataChange)) == before


def test_cross_table_ids_are_rejected(client, headers):
    a, b = create_table(client, headers), create_table(client, headers)
    field_id = str(uuid4())
    client.post(
        f"/api/data-tables/{a['id']}/fields",
        headers=headers,
        json={"id": field_id, "label": "备注", "field_type": "text", "expected_schema_version": 1},
    )
    response = client.patch(
        f"/api/data-tables/{b['id']}/fields/{field_id}",
        headers=headers,
        json={"expected_schema_version": 1, "label": "错表"},
    )
    assert response.status_code == 404


def test_no_delete_or_history_patch_endpoint(client, headers):
    t = create_table(client, headers)
    assert client.delete(f"/api/data-tables/{t['id']}", headers=headers).status_code == 405
    assert (
        client.patch(f"/api/data-tables/{t['id']}/changes", headers=headers, json={}).status_code
        == 405
    )


def test_unauthenticated_unknown_fields_and_missing_row(client, headers):
    assert client.get("/api/data-tables").status_code == 401
    response = client.post(
        "/api/data-tables",
        headers=headers,
        json={"id": str(uuid4()), "name": "非法", "created_at": "now"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"
    assert "输入" in response.json()["detail"]["message"]
    t = create_table(client, headers)
    assert (
        client.get(f"/api/data-tables/{t['id']}/records/{uuid4()}", headers=headers).status_code
        == 404
    )


def test_stale_versions_and_post_retry_have_no_duplicate_history(client, headers, db):
    body = {"id": str(uuid4()), "name": "只创建一次"}
    first = client.post("/api/data-tables", headers=headers, json=body)
    assert first.status_code == 200
    assert client.post("/api/data-tables", headers=headers, json=body).json()["id"] == body["id"]
    assert db.scalar(select(func.count()).select_from(DataChange)) == 1
    stale = client.patch(
        f"/api/data-tables/{body['id']}",
        headers=headers,
        json={"expected_schema_version": 99, "name": "覆盖"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "SCHEMA_CHANGED"
    assert db.scalar(select(func.count()).select_from(DataChange)) == 1


def test_record_update_reorder_archive_and_history_api(client, headers):
    t = create_table(client, headers)
    prefix = f"/api/data-tables/{t['id']}"
    field_id = str(uuid4())
    assert (
        client.post(
            prefix + "/fields",
            headers=headers,
            json={
                "id": field_id,
                "label": "描述",
                "field_type": "text",
                "expected_schema_version": 1,
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            prefix + "/fields/reorder",
            headers=headers,
            json={"field_ids": [field_id], "expected_schema_version": 2},
        ).status_code
        == 200
    )
    body = {"id": str(uuid4()), "schema_version": 3, "values": {field_id: "原值"}}
    created = client.post(prefix + "/records", headers=headers, json=body)
    assert created.status_code == 200
    assert client.post(prefix + "/records", headers=headers, json=body).json()["id"] == body["id"]
    record_url = prefix + f"/records/{body['id']}"
    changed = client.patch(
        record_url,
        headers=headers,
        json={"expected_revision": 1, "schema_version": 3, "values": {field_id: "新值"}},
    )
    assert changed.status_code == 200
    assert client.get(record_url, headers=headers).json()["values"][field_id] == "新值"
    assert (
        client.post(
            record_url + "/archive",
            headers=headers,
            json={"expected_revision": 2, "schema_version": 3},
        ).status_code
        == 200
    )
    assert (
        client.post(
            record_url + "/restore",
            headers=headers,
            json={"expected_revision": 3, "schema_version": 3},
        ).status_code
        == 200
    )
    changes = client.get(prefix + "/changes", headers=headers, params={"record_id": body["id"]})
    assert changes.status_code == 200
    assert changes.json()["total"] == 4
    assert all("creation_request" not in item for item in changes.json()["items"])


def test_unexpected_errors_do_not_log_private_values(client, headers, monkeypatch, caplog):
    t = create_table(client, headers)
    private = "PRIVATE_CUSTOM_ROW_70942"

    def fail(*args, **kwargs):
        raise RuntimeError(private)

    monkeypatch.setattr("apps.http.data_tables.service.create_record", fail)
    response = client.post(
        f"/api/data-tables/{t['id']}/records",
        headers=headers,
        json={"id": str(uuid4()), "schema_version": 1, "values": {"secret": private}},
    )
    assert response.status_code == 500
    assert private not in response.text
    assert private not in caplog.text


def test_openapi_covers_exact_public_actions(client):
    paths = client.get("/openapi.json").json()["paths"]
    prefix = "/api/data-tables"
    expected = {
        "": {"get", "post"},
        "/{table_id}": {"get", "patch"},
        "/{table_id}/fields": {"get", "post"},
        "/{table_id}/fields/reorder": {"post"},
        "/{table_id}/fields/{field_id}": {"patch"},
        "/{table_id}/records": {"get", "post"},
        "/{table_id}/records/{record_id}": {"get", "patch"},
        "/{table_id}/fields/{field_id}/targets": {"get"},
        "/{table_id}/changes": {"get"},
    }
    for entity in (
        "/{table_id}",
        "/{table_id}/fields/{field_id}",
        "/{table_id}/records/{record_id}",
    ):
        for action in ("archive", "restore"):
            expected[f"{entity}/{action}"] = {"post"}
    actual = {
        path[len(prefix) :]: set(methods)
        for path, methods in paths.items()
        if path.startswith(prefix)
    }
    assert actual == expected
