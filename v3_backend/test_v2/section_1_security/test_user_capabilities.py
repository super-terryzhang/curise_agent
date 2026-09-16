"""Section 1 — Security: per-user capability white-list (2026-06-22).

为什么这套测试存在：
    新增 `v3_user_capabilities` 表 + `require_capability` 依赖把
    "看财务" 这种敏感 feature 从 role-based 升级为白名单 ACL。
    破坏这条契约的回归会让 employee 突然能再看到财务 tab，或者
    被授权的人突然看不到 —— 都是产品决策级故障，必须用测试钉死。

锁定的契约：
    1. superadmin 永远能看（root bypass）
    2. 普通角色没 grant → 403
    3. 普通角色 grant 后 → 200
    4. revoke 立即生效 → 下次请求 403
    5. 未知 capability key → 400 (拦截 typo)
    6. /auth/me 返回当前用户的 capabilities 数组
    7. CRUD 端点只有 superadmin 能调

不测的：财务计算本身（section_4_orders 已覆盖）。这里只测 ACL。
"""

from __future__ import annotations

from domains.identity.models import UserCapability
from infrastructure.capabilities import CAP_FINANCIALS_VIEW
from test_v2.fixtures.helpers import login, seed_user


def _seed_order(db, *, user_id: int) -> int:
    """Minimal Order so the financial endpoint has something to look up.
    We don't care about the financial response — only the 401/403/200
    gate decision. Returns order id."""
    from domains.document.models import Document
    from domains.orders.models import Order

    doc = Document(
        user_id=user_id,
        filename="x.pdf",
        file_type="pdf",
        file_size_bytes=1,
        file_url="local/x.pdf",
        doc_type="purchase_order",
        status="extracted",
        extracted_data={"metadata": {}, "products": []},
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    order = Order(
        document_id=doc.id,
        user_id=user_id,
        filename="x.pdf",
        file_type="pdf",
        status="ready",
        po_number="X1",
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order.id


# ─── 1. superadmin auto-pass ─────────────────────────────────


def test_superadmin_can_view_financials_without_grant(client, db):
    """Root bypass: superadmin doesn't need a row in v3_user_capabilities."""
    su = seed_user(db, email="root@example.com", role="superadmin")
    headers = login(client, "root@example.com")
    order_id = _seed_order(db, user_id=su.id)

    r = client.get(f"/api/orders/{order_id}/financials", headers=headers)
    assert r.status_code in (200, 404, 500), r.text  # not 403
    assert r.status_code != 403, (
        "superadmin must bypass capability check; got 403 — root bypass broken"
    )


# ─── 2. employee without grant: 403 ──────────────────────────


def test_employee_without_capability_is_forbidden(client, db):
    """Cold-cutover default state: employee with no grant gets 403."""
    emp = seed_user(db, email="e1@example.com", role="employee")
    headers = login(client, "e1@example.com")
    order_id = _seed_order(db, user_id=emp.id)

    r = client.get(f"/api/orders/{order_id}/financials", headers=headers)
    assert r.status_code == 403, (
        f"employee without financials.view must get 403, got {r.status_code}"
    )
    assert "financials.view" in r.text or "权限" in r.text


# ─── 3. employee with grant: 200/404 (not 403) ───────────────


def test_employee_with_capability_passes_gate(client, db):
    """After a superadmin grants financials.view, the same employee
    passes the gate. The endpoint may still 404 if the order belongs to
    someone else — what we lock here is "no longer 403"."""
    emp = seed_user(db, email="e2@example.com", role="employee")
    headers = login(client, "e2@example.com")
    order_id = _seed_order(db, user_id=emp.id)
    db.add(
        UserCapability(user_id=emp.id, capability=CAP_FINANCIALS_VIEW)
    )
    db.commit()

    r = client.get(f"/api/orders/{order_id}/financials", headers=headers)
    assert r.status_code != 403, (
        f"granted user must NOT see 403; got {r.status_code} body={r.text}"
    )


# ─── 4. revoke takes effect immediately ──────────────────────


def test_revoking_capability_locks_user_out_immediately(client, db):
    """No caching: removing the row should flip behavior on the next request."""
    emp = seed_user(db, email="e3@example.com", role="employee")
    headers = login(client, "e3@example.com")
    order_id = _seed_order(db, user_id=emp.id)
    grant = UserCapability(user_id=emp.id, capability=CAP_FINANCIALS_VIEW)
    db.add(grant)
    db.commit()

    r1 = client.get(f"/api/orders/{order_id}/financials", headers=headers)
    assert r1.status_code != 403

    # Revoke
    db.delete(grant)
    db.commit()

    r2 = client.get(f"/api/orders/{order_id}/financials", headers=headers)
    assert r2.status_code == 403, (
        "after revoke the next request must 403; got %d — caching bug?"
        % r2.status_code
    )


# ─── 5. unknown capability key rejected at grant time ────────


def test_granting_unknown_capability_returns_400(client, db):
    seed_user(db, email="root@example.com", role="superadmin")
    headers = login(client, "root@example.com")
    target = seed_user(db, email="t@example.com", role="employee")

    r = client.post(
        f"/api/users/{target.id}/capabilities/totally.fake.key",
        headers=headers,
    )
    assert r.status_code == 400, r.text


# ─── 6. /auth/me carries capabilities array ──────────────────


def test_auth_me_returns_capabilities(client, db):
    emp = seed_user(db, email="e4@example.com", role="employee")
    headers = login(client, "e4@example.com")
    db.add(
        UserCapability(user_id=emp.id, capability=CAP_FINANCIALS_VIEW)
    )
    db.commit()

    r = client.get("/api/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "capabilities" in body, (
        "UserResponse missing `capabilities` field — frontend has nothing to gate on"
    )
    assert CAP_FINANCIALS_VIEW in body["capabilities"]


def test_auth_me_returns_empty_caps_when_none(client, db):
    seed_user(db, email="e5@example.com", role="employee")
    headers = login(client, "e5@example.com")
    r = client.get("/api/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["capabilities"] == []


# ─── 7. CRUD locked down to superadmin ───────────────────────


def test_non_superadmin_cannot_grant_capability(client, db):
    seed_user(db, email="admin@example.com", role="admin")
    headers = login(client, "admin@example.com")
    target = seed_user(db, email="x@example.com", role="employee")

    r = client.post(
        f"/api/users/{target.id}/capabilities/{CAP_FINANCIALS_VIEW}",
        headers=headers,
    )
    assert r.status_code == 403, (
        f"admin (not superadmin) must NOT be able to grant caps; got {r.status_code}"
    )


def test_non_superadmin_cannot_list_capabilities(client, db):
    seed_user(db, email="admin2@example.com", role="admin")
    headers = login(client, "admin2@example.com")
    target = seed_user(db, email="y@example.com", role="employee")

    r = client.get(f"/api/users/{target.id}/capabilities", headers=headers)
    assert r.status_code == 403


# ─── 8. Grant is idempotent ──────────────────────────────────


def test_double_grant_is_idempotent(client, db):
    """Double-clicking the checkbox or a network retry must NOT 409."""
    seed_user(db, email="root@example.com", role="superadmin")
    headers = login(client, "root@example.com")
    target = seed_user(db, email="t2@example.com", role="employee")

    r1 = client.post(
        f"/api/users/{target.id}/capabilities/{CAP_FINANCIALS_VIEW}",
        headers=headers,
    )
    r2 = client.post(
        f"/api/users/{target.id}/capabilities/{CAP_FINANCIALS_VIEW}",
        headers=headers,
    )
    assert r1.status_code in (200, 201), r1.text
    assert r2.status_code in (200, 201), (
        f"second grant must succeed (idempotent); got {r2.status_code}"
    )
