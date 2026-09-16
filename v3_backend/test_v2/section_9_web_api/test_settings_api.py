"""Section 9 — Web API: `/api/settings/*` admin settings HTTP contract.

测试目标：
    settings 路由是 admin 后台的核心配置面板：field-schemas、order-templates、
    supplier-templates、delivery-locations、company-config 全部 CRUD。
    Phase 6 的 tools/skills 还是 stub。这里验证完整契约 + admin-only RBAC。

为什么重要：
    - field-schemas 是订单提取的"字段词典"，seed-defaults 必须幂等。
    - company-config 是 upsert 语义——更新一个 key 不能洗掉别的。
    - Phase 6 的 tools/skills 是 stub，必须断言 501，否则将来 stub 被实现了
      但没人通知前端会爆炸。
    - employee 不能进 settings——admin-only 必须严格守住。

设计方法：
    所有写入用 `seed_user` 拿到的 superadmin token；403 测试用 employee
    `auth_tokens`。Phase 6 stub 测试就是直白的 status code 断言。
"""

from __future__ import annotations

from test_v2.fixtures.helpers import login


def _admin_headers(client, _seed_user) -> dict[str, str]:
    return login(client, "admin@example.com", "password123")


def _employee_headers(auth_tokens) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_tokens['access_token']}"}


# ═════ field-schemas ═════════════════════════════════════════


def test_seed_default_field_schema_is_idempotent(client, seed_user):
    """Seeding twice must NOT create a second default schema."""
    headers = _admin_headers(client, seed_user)
    r1 = client.post("/api/settings/field-schemas/seed-defaults", headers=headers)
    assert r1.status_code == 200
    sid1 = r1.json()["id"]

    r2 = client.post("/api/settings/field-schemas/seed-defaults", headers=headers)
    assert r2.status_code == 200
    sid2 = r2.json()["id"]

    assert sid1 == sid2, "seed_defaults must be idempotent"


def test_field_schema_crud_roundtrip(client, seed_user):
    """Create → Get → Update → Delete, asserting payload on each hop."""
    headers = _admin_headers(client, seed_user)

    # CREATE
    r = client.post(
        "/api/settings/field-schemas",
        json={"name": "MySchema", "description": "test"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["name"] == "MySchema"

    # GET
    r = client.get(f"/api/settings/field-schemas/{sid}", headers=headers)
    assert r.status_code == 200
    assert r.json()["id"] == sid

    # UPDATE
    r = client.put(
        f"/api/settings/field-schemas/{sid}",
        json={"name": "Renamed", "description": "new desc"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"

    # DELETE
    r = client.delete(f"/api/settings/field-schemas/{sid}", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"detail": "已删除"}

    # And it really is gone.
    r = client.get(f"/api/settings/field-schemas/{sid}", headers=headers)
    assert r.status_code == 404


def test_add_field_definition_to_existing_schema(client, seed_user):
    """POST /field-schemas/{id}/definitions appends a field definition."""
    headers = _admin_headers(client, seed_user)
    r = client.post(
        "/api/settings/field-schemas",
        json={"name": "WithFields"},
        headers=headers,
    )
    assert r.status_code == 201
    sid = r.json()["id"]

    r = client.post(
        f"/api/settings/field-schemas/{sid}/definitions",
        json={
            "field_key": "custom_field",
            "field_label": "Custom",
            "field_type": "string",
            "sort_order": 10,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["field_key"] == "custom_field"
    assert body["schema_id"] == sid


def test_delete_field_schema_cascades_definitions(client, seed_user):
    """Deleting a schema must drop its FieldDefinition rows too (cascade)."""
    headers = _admin_headers(client, seed_user)

    r = client.post(
        "/api/settings/field-schemas",
        json={"name": "Doomed"},
        headers=headers,
    )
    sid = r.json()["id"]

    rd = client.post(
        f"/api/settings/field-schemas/{sid}/definitions",
        json={"field_key": "doomed_field", "field_label": "x"},
        headers=headers,
    )
    assert rd.status_code == 201
    did = rd.json()["id"]

    # Delete the parent.
    r = client.delete(f"/api/settings/field-schemas/{sid}", headers=headers)
    assert r.status_code == 200

    # Touching the definition by id must 404 (schema gone → cascade).
    r = client.put(
        f"/api/settings/field-schemas/{sid}/definitions/{did}",
        json={"field_label": "y"},
        headers=headers,
    )
    assert r.status_code == 404


# ═════ order-templates ═══════════════════════════════════════


def test_order_template_crud_roundtrip(client, seed_user):
    headers = _admin_headers(client, seed_user)
    r = client.post(
        "/api/settings/order-templates",
        json={
            "name": "PO Template",
            "file_type": "excel",
            "header_row": 1,
            "data_start_row": 2,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    tid = r.json()["id"]

    r = client.get("/api/settings/order-templates", headers=headers)
    assert r.status_code == 200
    assert any(t["id"] == tid for t in r.json())

    r = client.put(
        f"/api/settings/order-templates/{tid}",
        json={"name": "Renamed PO", "is_active": False},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed PO"
    assert r.json()["is_active"] is False

    r = client.delete(f"/api/settings/order-templates/{tid}", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"detail": "已删除"}


# ═════ supplier-templates ═══════════════════════════════════


def test_supplier_template_crud_roundtrip(client, seed_user, db):
    """SupplierTemplate CRUD delegates to inquiry domain. Needs a real
    supplier_id (FK) to keep the inquiry validation happy."""
    from domains.masterdata.models import Supplier

    headers = _admin_headers(client, seed_user)
    sup = Supplier(name="ACME Foods", status=True)
    db.add(sup)
    db.commit()
    db.refresh(sup)

    r = client.post(
        "/api/settings/supplier-templates",
        json={
            "template_name": "ACME default",
            "supplier_id": sup.id,
            "field_positions": {"po_number": "A1"},
            "product_table_config": {"start_row": 10, "columns": {}},
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    tid = r.json()["id"]

    r = client.get("/api/settings/supplier-templates", headers=headers)
    assert r.status_code == 200
    assert any(t["id"] == tid for t in r.json())

    r = client.delete(f"/api/settings/supplier-templates/{tid}", headers=headers)
    assert r.status_code == 200


# ═════ delivery-locations ═══════════════════════════════════


def test_delivery_location_crud_roundtrip(client, seed_user):
    headers = _admin_headers(client, seed_user)

    r = client.post(
        "/api/settings/delivery-locations",
        json={
            "name": "Yokohama Pier 1",
            "address": "1 Pier Yokohama",
            "is_default": False,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    loc_id = r.json()["id"]
    assert r.json()["name"] == "Yokohama Pier 1"

    r = client.put(
        f"/api/settings/delivery-locations/{loc_id}",
        json={"address": "2 Pier Yokohama"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["address"] == "2 Pier Yokohama"

    r = client.delete(
        f"/api/settings/delivery-locations/{loc_id}", headers=headers
    )
    assert r.status_code == 200


# ═════ company-config (upsert semantics) ════════════════════


def test_company_config_upsert_does_not_clobber_other_keys(client, seed_user):
    """PUT /company-config items=[A] must NOT erase a previously-set key B.

    The upsert loop writes per-item; if implementation ever switches to
    "delete all then insert" this test will catch it.
    """
    headers = _admin_headers(client, seed_user)

    # Seed key A.
    r = client.put(
        "/api/settings/company-config",
        json={"items": [{"key": "company_name", "value": "ACME"}]},
        headers=headers,
    )
    assert r.status_code == 200

    # Update only key B; A must survive.
    r = client.put(
        "/api/settings/company-config",
        json={"items": [{"key": "billing_email", "value": "bill@acme.test"}]},
        headers=headers,
    )
    assert r.status_code == 200

    r = client.get("/api/settings/company-config", headers=headers)
    assert r.status_code == 200
    rows = {c["key"]: c["value"] for c in r.json()}
    assert rows.get("company_name") == "ACME", "key A must survive a key-B-only write"
    assert rows.get("billing_email") == "bill@acme.test"

    # And updating A flips the value (not "appends").
    r = client.put(
        "/api/settings/company-config",
        json={"items": [{"key": "company_name", "value": "ACME 2.0"}]},
        headers=headers,
    )
    assert r.status_code == 200
    r = client.get("/api/settings/company-config", headers=headers)
    rows = {c["key"]: c["value"] for c in r.json()}
    assert rows["company_name"] == "ACME 2.0"


# ═════ Phase 6 stubs (cheap insurance) ══════════════════════


def test_phase6_post_tools_seed_returns_zero(client, seed_user):
    """POST /settings/tools/seed currently returns {seeded: 0} (Phase 6 stub).
    If Phase 6 ever implements tool seeding, this test will tell us."""
    headers = _admin_headers(client, seed_user)
    r = client.post("/api/settings/tools/seed", headers=headers)
    assert r.status_code == 200
    assert r.json()["seeded"] == 0


def test_phase6_get_skills_returns_empty_list(client, seed_user):
    """GET /settings/skills currently returns []."""
    headers = _admin_headers(client, seed_user)
    r = client.get("/api/settings/skills", headers=headers)
    assert r.status_code == 200
    assert r.json() == []


def test_phase6_patch_tool_returns_501(client, seed_user):
    """PATCH /settings/tools/{name} → 501 (Phase 6b stub)."""
    headers = _admin_headers(client, seed_user)
    r = client.patch(
        "/api/settings/tools/some_tool",
        headers=headers,
    )
    assert r.status_code == 501


def test_phase6_post_skill_returns_501(client, seed_user):
    headers = _admin_headers(client, seed_user)
    r = client.post("/api/settings/skills", headers=headers)
    assert r.status_code == 501


# ═════ RBAC ═════════════════════════════════════════════════


def test_employee_blocked_from_settings_get(client, auth_tokens):
    """settings/* uses Admin guard — employee gets 403."""
    headers = _employee_headers(auth_tokens)
    r = client.get("/api/settings/field-schemas", headers=headers)
    assert r.status_code == 403


def test_employee_blocked_from_settings_create(client, auth_tokens):
    headers = _employee_headers(auth_tokens)
    r = client.post(
        "/api/settings/field-schemas",
        json={"name": "X"},
        headers=headers,
    )
    assert r.status_code == 403


def test_admin_can_read_settings(client, seed_user):
    """Sanity — admin role works where employee 403s."""
    headers = _admin_headers(client, seed_user)
    r = client.get("/api/settings/field-schemas", headers=headers)
    assert r.status_code == 200
