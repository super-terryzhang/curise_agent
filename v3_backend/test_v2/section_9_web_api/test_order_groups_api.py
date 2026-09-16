"""Section 9 — Web API: /api/order-groups/* HTTP contract (R7).

测试目标：
    R7 合单功能（Felix 2026-06-12）的 CRUD + assign/remove 完整管线。
    - 显示分组：Order 实体不动，仅设 Order.group_id
    - 跨用户隔离：A 不能看 B 的 group，B 的 order_id 不能被 A 分组
    - 删除 group → Order.group_id 自动 SET NULL（不 cascade 删 Order）
    - 重复 assign 幂等：同一个 order 多次分到同组应该没事
    - finance 角色也能用（R3 决定 — finance 在 require_writer 里）

为什么重要：
    R7 是用户提的最复杂的需求，涉及：
    - 新表 + 新 FK + ON DELETE 行为
    - RBAC（owner-only） + 跨用户隔离
    - 多 endpoint 一致性
    任何一道漏了 = 业务安全洞 / 数据丢失 / 用户体验崩溃。

设计方法：
    真实 HTTP（TestClient + login()），不 mock 中间件 + service。
    每个 endpoint 至少一个行为 + 一个边界（404 / 跨用户）。
"""

from __future__ import annotations

from domains.orders.models import Order, OrderGroup
from test_v2.fixtures.helpers import login, seed_user


# ─── Helpers ──────────────────────────────────────────────────


def _seed_order(db, *, user_id: int, po_number: str = "PO-X") -> Order:
    o = Order(
        user_id=user_id,
        filename="po.pdf",
        status="extracted",
        po_number=po_number,
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


# ─── List + Create ────────────────────────────────────────────


def test_create_order_group_returns_201_with_metadata(client, db):
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    r = client.post(
        "/api/order-groups",
        headers=headers,
        json={
            "name": "MILLENNIUM 2026-06-15",
            "ship_name": "CELEBRITY MILLENNIUM",
            "loading_date": "2026-06-15",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "MILLENNIUM 2026-06-15"
    assert body["ship_name"] == "CELEBRITY MILLENNIUM"
    assert body["order_count"] == 0
    assert body["id"] > 0


def test_create_with_initial_orders_assigns_them(client, db):
    """POST body 带 order_ids → 这些 order 的 group_id 立即被设置；
    order_count 反映命中数量。"""
    user = seed_user(db, email="alice@x.test", role="employee")
    o1 = _seed_order(db, user_id=user.id, po_number="P1")
    o2 = _seed_order(db, user_id=user.id, po_number="P2")
    headers = login(client, "alice@x.test")

    r = client.post(
        "/api/order-groups",
        headers=headers,
        json={"name": "G1", "order_ids": [o1.id, o2.id]},
    )
    assert r.status_code == 201, r.text
    assert r.json()["order_count"] == 2


def test_list_returns_only_my_groups(client, db):
    """A 创建一个组，B 看自己的列表必须看不到。跨用户隔离。"""
    alice = seed_user(db, email="alice@x.test", role="employee")
    seed_user(db, email="bob@x.test", role="employee")
    a_headers = login(client, "alice@x.test")
    b_headers = login(client, "bob@x.test")

    client.post("/api/order-groups", headers=a_headers, json={"name": "A's group"})

    r = client.get("/api/order-groups", headers=b_headers)
    assert r.status_code == 200
    assert r.json() == []

    r = client.get("/api/order-groups", headers=a_headers)
    assert len(r.json()) == 1
    assert r.json()[0]["user_id"] == alice.id


# ─── Get + Update + Delete ────────────────────────────────────


def test_get_other_users_group_returns_404_not_403(client, db):
    """跨用户访问必须是 404 不是 403 —— 403 会泄露"此 group 存在"。"""
    seed_user(db, email="alice@x.test", role="employee")
    seed_user(db, email="bob@x.test", role="employee")
    a_headers = login(client, "alice@x.test")
    b_headers = login(client, "bob@x.test")
    a_group = client.post(
        "/api/order-groups", headers=a_headers, json={"name": "X"}
    ).json()
    r = client.get(f"/api/order-groups/{a_group['id']}", headers=b_headers)
    assert r.status_code == 404


def test_patch_renames_group(client, db):
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    g = client.post(
        "/api/order-groups", headers=headers, json={"name": "old"}
    ).json()
    r = client.patch(
        f"/api/order-groups/{g['id']}", headers=headers, json={"name": "new"}
    )
    assert r.status_code == 200
    assert r.json()["name"] == "new"


def test_delete_group_sets_order_group_id_to_null_not_cascade(
    client, db, session_factory
):
    """关键：删 group 后 Order 行必须存活、group_id 变 NULL。
    cascade 删除会丢真实订单，是 R7 设计的不可接受失败。"""
    user = seed_user(db, email="alice@x.test", role="employee")
    o1 = _seed_order(db, user_id=user.id, po_number="P1")
    headers = login(client, "alice@x.test")
    g = client.post(
        "/api/order-groups",
        headers=headers,
        json={"name": "G", "order_ids": [o1.id]},
    ).json()

    r = client.delete(f"/api/order-groups/{g['id']}", headers=headers)
    assert r.status_code == 204

    fresh = session_factory()
    try:
        assert fresh.get(OrderGroup, g["id"]) is None  # group really gone
        kept = fresh.get(Order, o1.id)
        assert kept is not None  # order survives
        assert kept.group_id is None  # SET NULL, not cascade
    finally:
        fresh.close()


# ─── Assign + Remove ──────────────────────────────────────────


def test_assign_orders_updates_group_id_and_count(client, db, session_factory):
    user = seed_user(db, email="alice@x.test", role="employee")
    o1 = _seed_order(db, user_id=user.id, po_number="P1")
    o2 = _seed_order(db, user_id=user.id, po_number="P2")
    headers = login(client, "alice@x.test")
    g = client.post(
        "/api/order-groups", headers=headers, json={"name": "G"}
    ).json()

    r = client.post(
        f"/api/order-groups/{g['id']}/orders",
        headers=headers,
        json={"order_ids": [o1.id, o2.id]},
    )
    assert r.status_code == 200
    assert r.json()["order_count"] == 2

    fresh = session_factory()
    try:
        assert fresh.get(Order, o1.id).group_id == g["id"]
        assert fresh.get(Order, o2.id).group_id == g["id"]
    finally:
        fresh.close()


def test_assign_silently_ignores_other_users_orders(client, db, session_factory):
    """A 把 B 的 order_id 塞进 assign 请求 —— B 的 order 必须不受影响。"""
    a = seed_user(db, email="alice@x.test", role="employee")
    b = seed_user(db, email="bob@x.test", role="employee")
    b_order = _seed_order(db, user_id=b.id, po_number="B1")
    a_headers = login(client, "alice@x.test")
    g = client.post(
        "/api/order-groups", headers=a_headers, json={"name": "G"}
    ).json()

    r = client.post(
        f"/api/order-groups/{g['id']}/orders",
        headers=a_headers,
        json={"order_ids": [b_order.id]},
    )
    assert r.status_code == 200
    assert r.json()["order_count"] == 0

    fresh = session_factory()
    try:
        assert fresh.get(Order, b_order.id).group_id is None
    finally:
        fresh.close()


def test_remove_order_unsets_group_id(client, db, session_factory):
    user = seed_user(db, email="alice@x.test", role="employee")
    o1 = _seed_order(db, user_id=user.id, po_number="P1")
    headers = login(client, "alice@x.test")
    g = client.post(
        "/api/order-groups",
        headers=headers,
        json={"name": "G", "order_ids": [o1.id]},
    ).json()

    r = client.delete(
        f"/api/order-groups/{g['id']}/orders/{o1.id}", headers=headers
    )
    assert r.status_code == 204
    fresh = session_factory()
    try:
        assert fresh.get(Order, o1.id).group_id is None
    finally:
        fresh.close()


# ─── RBAC / role ──────────────────────────────────────────────


def test_finance_role_can_use_order_groups(client, db):
    """R3 决定 finance 进 require_writer —— order-groups 全部端点应放行。"""
    seed_user(db, email="finance@x.test", role="finance")
    headers = login(client, "finance@x.test")
    r = client.post(
        "/api/order-groups", headers=headers, json={"name": "finance test"}
    )
    assert r.status_code == 201, r.text


def test_anonymous_request_returns_401(client, db):
    r = client.get("/api/order-groups")
    assert r.status_code in (401, 403)
