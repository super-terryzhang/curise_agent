"""Section 9 — Web API: /api/orders/{id}/financials* HTTP contract.

测试目标：
    `apps/http/order_financials.py` 的 6 个端点（GET financials, POST/PATCH/
    DELETE cost-items, PATCH financial-settings, GET financials/export.xlsx）
    必须能端到端跑通。

为什么重要：
    `compute_pnl` 的纯函数测试 + `service` 层测试都过了，但 HTTP layer 没人
    测过 —— 5/29 deploy 时 `service.py` 漏了 `from sqlalchemy import select`
    import（refactor 时清理过头），所有 `/financials` 请求 500。pytest 没抓到
    因为压根没人 end-to-end 调过 HTTP 这条路径。

    这个文件钉死 6 个端点的 happy path + 一个 cross-user 隔离。
"""

from __future__ import annotations

from domains.identity.models import User, UserCapability
from domains.orders.models import Order
from test_v2.fixtures.helpers import login
from test_v2.fixtures.helpers import seed_user as _seed_user


def seed_user(db, **kwargs):
    """These cases exercise authorized financial operations and object isolation."""
    user = _seed_user(db, **kwargs)
    db.add(UserCapability(user_id=user.id, capability="financials.view"))
    db.commit()
    return user


# ─── Fixtures ────────────────────────────────────────────────


def _seed_order_with_match(db, owner_email: str, currency: str = "USD") -> Order:
    """Create an Order with 2 matched products for the given user."""
    user = db.query(User).filter_by(email=owner_email).one()
    order = Order(
        user_id=user.id,
        filename="finbook.pdf",
        file_type="pdf",
        status="ready_for_review",
        po_number="PO-FIN-API-001",
        ship_name="MV Profit",
        currency=currency,
        delivery_date="2026-06-30",
        match_results=[
            {
                "product_code": "X1",
                "product_name": "Item X1",
                "quantity": 10,
                "unit_price": 25.0,
                "matched_product": {"id": 1, "supplier_id": 100, "code": "X1", "price": 15.0},
            },
            {
                "product_code": "X2",
                "product_name": "Item X2",
                "quantity": 4,
                "unit_price": 50.0,
                "matched_product": {"id": 2, "supplier_id": 100, "code": "X2", "price": 30.0},
            },
        ],
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


# ─── GET /api/orders/{id}/financials ─────────────────────────


def test_get_financials_returns_200_with_full_pnl_shape(client, db):
    """GET /financials happy path — must return summary + product_lines +
    cost_items + meta. This is the test that would have caught the
    missing `select` import bug.
    """
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _seed_order_with_match(db, "alice@example.com")

    r = client.get(f"/api/orders/{order.id}/financials", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()

    # Shape contract — these keys are what the frontend FinancialTab reads.
    for key in ("display_currency", "order_currency", "tax_rate", "summary",
                "product_lines", "cost_items", "warnings", "meta"):
        assert key in body, f"missing key: {key}"

    s = body["summary"]
    # Math: 10×25 + 4×50 = 450 revenue; 10×15 + 4×30 = 270 cost; gross = 180.
    # Default tax_rate is 0.06 → tax = 10.80; net = 169.20.
    assert s["product_revenue"] == 450.0
    assert s["product_cost"] == 270.0
    assert s["extra_costs_total"] == 0.0
    assert s["gross_profit"] == 180.0
    assert round(s["tax_amount"], 2) == 10.80
    assert round(s["net_profit"], 2) == 169.20

    # Cost items empty until we POST one
    assert body["cost_items"] == []
    # 2 product lines, both matched
    assert len(body["product_lines"]) == 2
    assert all(p["matched"] for p in body["product_lines"])

    # Meta block carries order context
    meta = body["meta"]
    assert meta["order_id"] == order.id
    assert meta["po_number"] == "PO-FIN-API-001"
    assert meta["ship_name"] == "MV Profit"


def test_get_financials_other_user_returns_404(client, db):
    """Cross-user: bob cannot view alice's order financials."""
    seed_user(db, email="alice@example.com", role="employee")
    seed_user(db, email="bob@example.com", role="employee")
    order = _seed_order_with_match(db, "alice@example.com")
    bob_headers = login(client, "bob@example.com")

    r = client.get(f"/api/orders/{order.id}/financials", headers=bob_headers)
    assert r.status_code == 404


# ─── POST /api/orders/{id}/cost-items ────────────────────────


def test_create_cost_item_updates_financials(client, db):
    """POST /cost-items → 201; subsequent GET /financials shows it in
    extra_costs_total + total_cost + reduced gross_profit."""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _seed_order_with_match(db, "alice@example.com")

    r = client.post(
        f"/api/orders/{order.id}/cost-items",
        json={"category": "运费", "amount": 50.0, "currency": "USD", "notes": "DHL"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["category"] == "运费"
    assert float(item["amount"]) == 50.0
    assert item["currency"] == "USD"
    item_id = item["id"]

    # Re-fetch financials: extra_costs_total should now be 50.0.
    fin = client.get(f"/api/orders/{order.id}/financials", headers=headers).json()
    s = fin["summary"]
    assert s["extra_costs_total"] == 50.0
    # gross_profit drops by 50 (450 - 270 - 50 = 130)
    assert s["gross_profit"] == 130.0
    # cost_items returned
    assert len(fin["cost_items"]) == 1
    assert fin["cost_items"][0]["id"] == item_id


# ─── PATCH /api/orders/{id}/cost-items/{item_id} ─────────────


def test_update_cost_item_changes_amount(client, db):
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _seed_order_with_match(db, "alice@example.com")
    item = client.post(
        f"/api/orders/{order.id}/cost-items",
        json={"category": "运费", "amount": 50.0, "currency": "USD"},
        headers=headers,
    ).json()

    r = client.patch(
        f"/api/orders/{order.id}/cost-items/{item['id']}",
        json={"amount": 75.0},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert float(r.json()["amount"]) == 75.0

    fin = client.get(f"/api/orders/{order.id}/financials", headers=headers).json()
    assert fin["summary"]["extra_costs_total"] == 75.0


# ─── DELETE /api/orders/{id}/cost-items/{item_id} ────────────


def test_delete_cost_item_removes_it(client, db):
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _seed_order_with_match(db, "alice@example.com")
    item = client.post(
        f"/api/orders/{order.id}/cost-items",
        json={"category": "运费", "amount": 50.0, "currency": "USD"},
        headers=headers,
    ).json()

    r = client.delete(
        f"/api/orders/{order.id}/cost-items/{item['id']}",
        headers=headers,
    )
    assert r.status_code == 204

    fin = client.get(f"/api/orders/{order.id}/financials", headers=headers).json()
    assert fin["cost_items"] == []
    assert fin["summary"]["extra_costs_total"] == 0.0


# ─── PATCH /api/orders/{id}/financial-settings ───────────────


def test_update_settings_changes_tax_rate(client, db):
    """PATCH tax_rate → returns full P&L with new tax applied."""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _seed_order_with_match(db, "alice@example.com")

    r = client.patch(
        f"/api/orders/{order.id}/financial-settings",
        json={"tax_rate": 0.10},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tax_rate"] == 0.10
    # gross 180 × 10% = 18.0
    assert round(body["summary"]["tax_amount"], 2) == 18.0


# ─── GET /api/orders/{id}/financials/export.xlsx ─────────────


def test_export_xlsx_returns_workbook(client, db):
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _seed_order_with_match(db, "alice@example.com")

    r = client.get(f"/api/orders/{order.id}/financials/export.xlsx", headers=headers)
    assert r.status_code == 200, r.text
    # Real xlsx blob starts with PK (zip header)
    assert r.content[:2] == b"PK"
    assert "spreadsheetml.sheet" in r.headers["content-type"]
    # Filename header carries the PO number
    assert "PO-FIN-API-001" in r.headers["content-disposition"]
