"""HTTP authorization and lifecycle contract for reusable conversion rules."""

from domains.masterdata.models import UnitConversionRule
from test_v2.fixtures.helpers import login, seed_user


def source_rule_payload(**overrides):
    payload = {
        "scope_type": "source_unit",
        "source_system": "oracle",
        "source_unit": "KG2.2",
        "target_unit": "KG",
        "source_quantity": "1",
        "target_quantity": "1",
        "evidence": "PO and supplier units checked",
    }
    payload.update(overrides)
    return payload


def test_admin_rule_lifecycle_and_writer_list_access(client, db):
    """Removing role/revision gates would make global rules unaudited."""

    admin = seed_user(db, email="conversion-admin@example.com", role="admin")
    seed_user(db, email="conversion-writer@example.com", role="employee")
    admin_headers = login(client, admin.email)
    writer_headers = login(client, "conversion-writer@example.com")

    created_response = client.post(
        "/api/data/unit-conversion-rules",
        json=source_rule_payload(),
        headers=admin_headers,
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    assert created["status"] == "draft"
    assert created["revision"] == 1

    listed = client.get("/api/data/unit-conversion-rules", headers=writer_headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["id"] == created["id"]

    verified_response = client.patch(
        f"/api/data/unit-conversion-rules/{created['id']}/verify",
        json={"expected_revision": 1, "evidence": "管理员复核供应商资料"},
        headers=admin_headers,
    )
    assert verified_response.status_code == 200, verified_response.text
    verified = verified_response.json()
    assert verified["status"] == "verified"
    assert verified["verified_by"] == admin.id
    assert verified["revision"] == 2

    stale = client.patch(
        f"/api/data/unit-conversion-rules/{created['id']}/retire",
        json={"expected_revision": 1, "evidence": "stale request"},
        headers=admin_headers,
    )
    assert stale.status_code == 409
    assert "已被其他操作修改" in stale.json()["detail"]

    retired = client.patch(
        f"/api/data/unit-conversion-rules/{created['id']}/retire",
        json={"expected_revision": 2, "evidence": "供应商包装已更新"},
        headers=admin_headers,
    )
    assert retired.status_code == 200, retired.text
    assert retired.json()["status"] == "retired"
    assert retired.json()["revision"] == 3


def test_employee_cannot_mutate_reusable_rules(client, db):
    """Replacing Admin with Writer would allow an order user to change all products."""

    seed_user(db, email="conversion-employee@example.com", role="employee")
    headers = login(client, "conversion-employee@example.com")

    response = client.post(
        "/api/data/unit-conversion-rules",
        json=source_rule_payload(),
        headers=headers,
    )

    assert response.status_code == 403
    assert db.query(UnitConversionRule).count() == 0

def test_invalid_rule_business_values_return_chinese_400(client, db):
    """Letting schema 422 leak would give users no actionable Chinese explanation."""

    seed_user(db, email="conversion-invalid@example.com", role="admin")
    headers = login(client, "conversion-invalid@example.com")

    cases = [
        (source_rule_payload(scope_type="wildcard"), "作用域"),
        (source_rule_payload(source_quantity="0"), "换算数量"),
        (source_rule_payload(evidence="   "), "审核依据"),
    ]
    for payload, expected in cases:
        response = client.post(
            "/api/data/unit-conversion-rules", json=payload, headers=headers
        )
        assert response.status_code == 400, response.text
        assert expected in response.json()["detail"]

    assert db.query(UnitConversionRule).count() == 0
