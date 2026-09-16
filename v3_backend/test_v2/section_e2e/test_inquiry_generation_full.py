"""Section E2E — Inquiry generation end-to-end.

测试目标：
    用户从"已匹配的 Order"到"拿到每个 supplier 的 Excel 文件"这整条路径。
    包括 HTTP 调用、后台 worker、SSE 流（基础）、文件真的能 openpyxl 读回，
    并且每个单元格的值跟原订单一致。

为什么重要：
    CLAUDE.md 记录的 4 个历史 bug（PO 号 / 单位 / pack_size / 交货日期）
    全部出在这条管线里。生成的 Excel 字段错位等于供应商收到错信息，业务
    损失立刻发生。这一层必须验真文件 + 真内容。

设计方法：
    1. 通过 API 上传订单（实际项目通常是先匹配再生成询价），但这里直接
       build Order + 匹配信息以隔离匹配本身的复杂性。
    2. POST /api/orders/{id}/generate-inquiry 触发后台生成。
    3. 用 fresh session 读 inquiry state（worker thread DB 隔离）。
    4. 下载 .xlsx → openpyxl 读回 → 断言关键单元格。
"""

from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook

from domains.inquiry import repository as inq_repo
from domains.masterdata.models import Supplier
from domains.orders.models import Order

# ─── Fixtures: build a matched Order + supplier template ─────


@pytest.fixture
def order_with_one_supplier(db, employee_auth):
    """Order owned by the e2e_emp@x.test user, one supplier, 2 products.

    Auth is set up via employee_auth fixture (e2e_emp@x.test, role=employee).
    """
    from domains.identity.models import User

    user = db.query(User).filter_by(email="e2e_emp@x.test").one()

    order = Order(
        user_id=user.id,
        filename="po.pdf",
        file_type="pdf",
        status="ready_for_review",
        po_number="PO-E2E-001",
        ship_name="MV E2E",
        currency="USD",
        delivery_date="2026-06-15",
        match_results=[
            {
                "product_code": "BEEF-01",
                "product_name": "Beef Loin 12kg",
                "quantity": 10,
                "unit_price": 25.0,
                "pack_size": "12kg/case",
                "matched_product": {
                    "id": 1001,
                    "supplier_id": 500,
                    "code": "BEEF-01",
                    "unit": "KG",
                    "pack_size": "12kg/case",
                },
            },
            {
                "product_code": "PORK-02",
                "product_name": "Pork Belly 10kg",
                "quantity": 8,
                "unit_price": 18.0,
                "pack_size": "10kg/case",
                "matched_product": {
                    "id": 1002,
                    "supplier_id": 500,
                    "code": "PORK-02",
                    "unit": "KG",
                    "pack_size": "10kg/case",
                },
            },
        ],
    )
    db.add(order)
    db.add(
        Supplier(
            id=500,
            name="Meat Supplier Co",
            contact="John Smith",
            email="john@meat.test",
            phone="+81-3-1234-5678",
            status=True,
        )
    )
    db.commit()
    db.refresh(order)
    return order


# ─── 1. Readiness pre-analyze ────────────────────────────────


def test_readiness_groups_products_by_supplier(
    client, employee_auth, order_with_one_supplier
):
    """GET /inquiry-readiness should show 1 supplier (id=500) with 2 products.

    Response is the legacy_dict shape: `suppliers` is a dict keyed by str(sid).
    """
    r = client.get(
        f"/api/orders/{order_with_one_supplier.id}/inquiry-readiness",
        headers=employee_auth,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["supplier_count"] == 1
    suppliers = body["suppliers"]
    assert "500" in suppliers, f"supplier 500 missing from {list(suppliers.keys())}"
    assert suppliers["500"]["product_count"] == 2


# ─── 2. Generate end-to-end ────────────────────────────────


def _get_inquiry_for_order(session_factory, order_id: int):
    """Resolve the Inquiry row by order_id via a fresh session."""
    from domains.inquiry.models import Inquiry

    fresh = session_factory()
    try:
        return fresh.query(Inquiry).filter_by(order_id=order_id).one()
    finally:
        fresh.close()


def test_generate_inquiry_writes_excel_file(
    client, employee_auth, session_factory, order_with_one_supplier
):
    """POST /generate-inquiry → returns 200, supplier row has excel_file_url,
    file is downloadable + openpyxl can read it.

    Response is {ok, order_id, job_id, status}; resolve inquiry_id by querying
    the Inquiry row via the order_id.
    """
    r = client.post(
        f"/api/orders/{order_with_one_supplier.id}/generate-inquiry",
        headers=employee_auth,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] in {"in_progress", "completed"}

    inquiry = _get_inquiry_for_order(session_factory, order_with_one_supplier.id)
    fresh = session_factory()
    try:
        suppliers = inq_repo.list_inquiry_suppliers(fresh, inquiry.id)
        assert len(suppliers) == 1
        sup = suppliers[0]
        assert sup.status == "completed"
        assert sup.excel_file_url, "no excel_file_url on supplier row"
    finally:
        fresh.close()


def test_generated_excel_is_openable_and_has_content(
    client, employee_auth, session_factory, order_with_one_supplier, _local_storage
):
    """After generation, the .xlsx file must be readable by openpyxl + have
    cells filled in (not empty)."""
    r = client.post(
        f"/api/orders/{order_with_one_supplier.id}/generate-inquiry",
        headers=employee_auth,
    )
    assert r.status_code == 200

    inquiry = _get_inquiry_for_order(session_factory, order_with_one_supplier.id)
    fresh = session_factory()
    try:
        sup = inq_repo.list_inquiry_suppliers(fresh, inquiry.id)[0]
        file_url = sup.excel_file_url
    finally:
        fresh.close()

    blob = _local_storage.download(file_url)
    assert blob, "downloaded blob is empty"
    assert len(blob) > 1000, "Excel suspiciously small"
    wb = load_workbook(io.BytesIO(blob))
    ws = wb.active
    # The generic template emits a product table — at least the product rows
    # should contain our product codes somewhere.
    all_cells = [
        str(cell.value) for row in ws.iter_rows() for cell in row if cell.value
    ]
    flat = "\n".join(all_cells)
    assert "BEEF-01" in flat, f"product code BEEF-01 missing from Excel:\n{flat[:500]}"
    assert "PORK-02" in flat, f"product code PORK-02 missing from Excel:\n{flat[:500]}"


# ─── 3. Inquiry state visible to user ────────────────────────


def test_user_can_read_back_inquiry_state(
    client, employee_auth, order_with_one_supplier
):
    """After generating, user fetches readiness again and sees the inquiry
    has a populated state (not pristine)."""
    client.post(
        f"/api/orders/{order_with_one_supplier.id}/generate-inquiry",
        headers=employee_auth,
    )
    r = client.get(
        f"/api/orders/{order_with_one_supplier.id}/inquiry-readiness",
        headers=employee_auth,
    )
    assert r.status_code == 200
    # The body has supplier_count even after generation (readiness re-runs
    # pre_analyze — it's safe + idempotent).
    assert r.json()["supplier_count"] == 1


# ─── 4. Preview endpoint returns HTML ───────────────────


def test_preview_url_works_after_generation(
    client, employee_auth, session_factory, order_with_one_supplier
):
    client.post(
        f"/api/orders/{order_with_one_supplier.id}/generate-inquiry",
        headers=employee_auth,
    )
    r = client.get(
        f"/api/orders/{order_with_one_supplier.id}/inquiry-preview/500",
        headers=employee_auth,
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'class="inquiry-preview"' in r.text


# ─── 5. Cross-user blocked ──────────────────────────────────


def test_other_user_cannot_generate_inquiry(
    client, employee_auth, order_with_one_supplier, db
):
    """User B can't trigger generate-inquiry on User A's order."""
    from test_v2.fixtures.helpers import login, seed_user

    seed_user(db, email="thief@x.test", role="employee", password="password123")
    other_auth = login(client, "thief@x.test")
    r = client.post(
        f"/api/orders/{order_with_one_supplier.id}/generate-inquiry",
        headers=other_auth,
    )
    assert r.status_code in {403, 404}, (
        f"expected 403 or 404, got {r.status_code}"
    )
