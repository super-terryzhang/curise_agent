"""Section 9 — Web API: /api/orders/{id}/inquiry-* HTTP contract (Phase 5).

测试目标：
    `apps/http/inquiry.py` 是询价生成 + 预览 + 取消 + override 的 8 个端点
    的薄壳，下面接 `domains.inquiry.orchestrator`。验证：
      - readiness 返回 supplier 分组 + 模板选择
      - generate-inquiry 返回 in_progress + job_id（其实是 200 OK 而不是
        202——这是该 router 的真实契约）
      - inquiry-preview 在生成前 404，生成后返回 URL
      - cancel-inquiry / overrides / 单 supplier redo 端点的行为

为什么重要：
    - 询价是订单工作流的最后一公里。生成耗时 5-30s，前端只能用 SSE/
      polling 跟进。任何 HTTP 契约漂移（200 vs 202、字段名变动）都会让
      前端永远卡在「正在生成…」。
    - inquiry-preview before generation 必须 404 不能 500（前端做"是否
      生成过"判断的依据）。
    - field-overrides 是 Phase 5 stub——必须返 200 不是 501，因前端默认
      会在每次生成前 POST 一次 overrides；返 501 会把整个工作流 toast 报错。

设计方法：
    - 直接 build Order 行（user_id = 当前 employee）+ Supplier +
      SupplierTemplate + match_results。
    - 使用 conftest 的 SynchronousRunner，调 generate-inquiry 后 _job
      synchronously 跑完整 orchestrator → 真实 LocalFileStorage 落盘。
"""

from __future__ import annotations

import pytest

from apps.http import _inquiry_streams
from domains.inquiry import repository as inquiry_repo
from domains.inquiry.models import Inquiry, SupplierTemplate
from domains.masterdata.models import Supplier
from domains.orders.models import Order
from test_v2.fixtures.helpers import login, seed_user


@pytest.fixture(autouse=True)
def _clear_inquiry_stream_registry():
    """`_inquiry_streams._streams` 是模块级全局 dict——跨测试残留会让
    cancel-inquiry 端点把上一条测试遗留的 handle 当成"有效 stream"，
    导致 404 测试假阳性。每个测试前后都清空一次。"""
    _inquiry_streams._streams.clear()
    yield
    _inquiry_streams._streams.clear()


# ─── Helpers ──────────────────────────────────────────────────


def _make_order_with_matched_products(db, *, user_id: int, supplier_id: int = 100) -> Order:
    """Build an Order owned by `user_id`, with 2 matched products at `supplier_id`."""
    order = Order(
        user_id=user_id,
        filename="po.pdf",
        file_type="pdf",
        status="ready_for_review",
        po_number="PO-9-001",
        ship_name="MV Test",
        currency="USD",
        delivery_date="2026-06-15",
        match_results=[
            {
                "product_code": "A1",
                "product_name": "Apple",
                "quantity": 5,
                "unit_price": 2.0,
                "matched_product": {
                    "id": 11,
                    "supplier_id": supplier_id,
                    "code": "A1",
                    "unit": "kg",
                },
            },
            {
                "product_code": "A2",
                "product_name": "Apricot",
                "quantity": 3,
                "unit_price": 4.0,
                "matched_product": {
                    "id": 12,
                    "supplier_id": supplier_id,
                    "code": "A2",
                    "unit": "kg",
                },
            },
        ],
    )
    db.add(order)
    db.add(
        Supplier(
            id=supplier_id,
            name="Apple Inc",
            contact="A Buyer",
            email="a@x.com",
            phone="1",
            status=True,
        )
    )
    db.commit()
    db.refresh(order)
    return order


def _make_supplier_template(db, *, supplier_id: int) -> SupplierTemplate:
    tpl = SupplierTemplate(
        template_name="Apple Template",
        supplier_ids=[supplier_id],
        supplier_id=supplier_id,
        template_styles={"zones": {"meta": {}}},
        field_positions={"po_number": "A1"},
        product_table_config={
            "start_row": 10,
            "columns": {"A": "product_code", "B": "quantity"},
        },
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


# ─── GET /inquiry-readiness ────────────────────────────────────


def test_readiness_groups_products_by_supplier(client, db):
    """readiness 返回 v2-shape inquiry_data dict：supplier_count + suppliers."""
    user = seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _make_order_with_matched_products(db, user_id=user.id)
    _make_supplier_template(db, supplier_id=100)

    r = client.get(f"/api/orders/{order.id}/inquiry-readiness", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["supplier_count"] == 1
    assert "suppliers" in body
    sup = body["suppliers"].get("100")
    assert sup is not None
    assert sup["product_count"] == 2
    assert sup["template"]["id"] is not None
    assert sup["template"]["selection_method"] == "exact"


def test_readiness_unknown_order_returns_404(client, db):
    """订单不存在 → 404。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    r = client.get("/api/orders/999999/inquiry-readiness", headers=headers)
    assert r.status_code == 404


# ─── POST /generate-inquiry (full run) ─────────────────────────


def test_generate_inquiry_returns_in_progress(client, db):
    """端点立刻返回 200 + {ok, order_id, job_id, status}（不是 202——这是
    apps/http/inquiry.py L139 的实际契约）。"""
    user = seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _make_order_with_matched_products(db, user_id=user.id)
    _make_supplier_template(db, supplier_id=100)

    r = client.post(f"/api/orders/{order.id}/generate-inquiry", headers=headers)
    # FastAPI default is 200 — the route does NOT explicitly return 202.
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["order_id"] == order.id
    assert body["status"] == "in_progress"
    assert body["job_id"]


def test_generate_then_preview_returns_html(client, db, session_factory):
    """生成完毕（SynchronousRunner 同步跑完 orchestrator）→ inquiry-preview
    端点应返 200 + HTML 内容。

    历史：早期返回 JSON 元数据 `{excel_file_url, ...}`，但 v3 前端的
    `getInquiryPreview` 用 `res.text()` 当 HTML 渲染到弹窗里，结果用户
    看到原始 JSON 字符串。修复后端真返回 HTML。"""
    user = seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _make_order_with_matched_products(db, user_id=user.id)
    _make_supplier_template(db, supplier_id=100)

    gen = client.post(f"/api/orders/{order.id}/generate-inquiry", headers=headers)
    assert gen.status_code == 200

    # SynchronousRunner already finished — inquiry row should exist with the
    # supplier's file URL filled in.
    fresh = session_factory()
    try:
        inquiry = fresh.query(Inquiry).filter(Inquiry.order_id == order.id).one()
        rows = inquiry_repo.list_inquiry_suppliers(fresh, inquiry.id)
        assert rows
        # All suppliers should have an excel_file_url (status completed).
        assert all(r.excel_file_url for r in rows if r.status == "completed")
    finally:
        fresh.close()

    r = client.get(f"/api/orders/{order.id}/inquiry-preview/100", headers=headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html")
    # The preview is real HTML — no JSON envelope.
    assert not r.text.lstrip().startswith("{")


# ─── GET /inquiry-preview before generation ───────────────────


def test_preview_before_generation_returns_404(client, db):
    """从未生成过询价 → state 为 None → 404 「尚未生成询价」。"""
    user = seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _make_order_with_matched_products(db, user_id=user.id)

    r = client.get(f"/api/orders/{order.id}/inquiry-preview/100", headers=headers)
    assert r.status_code == 404


# ─── POST /cancel-inquiry ─────────────────────────────────────


def test_cancel_without_active_run_returns_404(client, db):
    """从未启动过询价 → 没有 stream + orchestrator.request_cancel 也抛
    NotFound → 404。"""
    user = seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _make_order_with_matched_products(db, user_id=user.id)

    r = client.post(f"/api/orders/{order.id}/cancel-inquiry", headers=headers)
    assert r.status_code == 404


# ─── POST /inquiry-field-overrides (Phase 5 stub) ─────────────


def test_field_overrides_accepted_as_stub(client, db):
    """Phase 5 stub：必须返 200 + {ok: true, applied: false} —— 前端会在
    每次生成前自动 POST overrides，若返 501 整个工作流崩。"""
    user = seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _make_order_with_matched_products(db, user_id=user.id)

    r = client.post(
        f"/api/orders/{order.id}/inquiry-field-overrides/100",
        json={"po_number": "PO-OVR-1"},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["applied"] is False
    assert body["supplier_id"] == 100


# ─── POST /generate-inquiry/{supplier_id} (single redo) ───────


def test_single_supplier_generate_returns_in_progress(client, db):
    """单 supplier redo 端点：返 200 + supplier_id。"""
    user = seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _make_order_with_matched_products(db, user_id=user.id)
    _make_supplier_template(db, supplier_id=100)

    r = client.post(
        f"/api/orders/{order.id}/generate-inquiry/100", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["order_id"] == order.id
    assert body["supplier_id"] == 100
    assert body["status"] == "in_progress"
    assert body["job_id"]


def test_single_supplier_unknown_supplier_still_returns_200(client, db):
    """未知 supplier_id：orchestrator 在背景 thread 抛 BadRequest，
    被 _job 的 except Exception 吞掉并 emit 到 sink。HTTP 端点本身
    永远 200——这是异步任务模型的副作用，需要钉死。"""
    user = seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _make_order_with_matched_products(db, user_id=user.id)

    r = client.post(
        f"/api/orders/{order.id}/generate-inquiry/9999", headers=headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "in_progress"


# ─── GET /inquiry-data-preview ────────────────────────────────


def test_data_preview_returns_flat_shape(client, db):
    """生成后查询 data-preview：顶层平铺，没有 `data` 包裹。

    历史：早期返 `{order_id, supplier_id, data: {...}}` 嵌套，但 v3 前端
    的 `InquiryDataPreview` TS 类型期望顶层字段，调 `preview.template.name`
    立即崩 "Cannot read properties of undefined (reading 'name')"。修复后
    后端把字段平铺到顶层。"""
    user = seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _make_order_with_matched_products(db, user_id=user.id)
    _make_supplier_template(db, supplier_id=100)

    # readiness 触发 pre_analyze 写入 supplier row（status=pending）
    client.get(f"/api/orders/{order.id}/inquiry-readiness", headers=headers)

    r = client.get(
        f"/api/orders/{order.id}/inquiry-data-preview/100", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # No legacy `data` wrapper — this is the sentinel that caught the regression.
    assert "data" not in body
    # Top-level identity fields.
    assert body["supplier_id"] == 100
    # Total + product count both reflect the supplier's match_results.
    assert body["total_products"] == 2
    assert len(body["products"]) == 2


def test_data_preview_before_any_inquiry_returns_404(client, db):
    """完全没调过 readiness/generate → state 为 None → 404。"""
    user = seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _make_order_with_matched_products(db, user_id=user.id)

    r = client.get(
        f"/api/orders/{order.id}/inquiry-data-preview/100", headers=headers
    )
    assert r.status_code == 404
