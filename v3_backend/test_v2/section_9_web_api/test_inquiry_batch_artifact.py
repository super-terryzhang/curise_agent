"""Section 9 — Web API: GET /api/artifacts/inquiry-batch/{order_id}.

测试目标:
    新的 inquiry_batch_card artifact 后端契约 —— 该 endpoint 把
    domains.inquiry.service.read_inquiry_state() 投射成 frontend
    InquiryBatchCard.tsx 消费的 shape。每个测试 pin 住一个不可变规则:

      A. 跨用户隔离 —— 别人的订单返回 404 (而非 403)。我们不向非
         owner 透露订单是否存在。
      B. 订单存在但没起过 inquiry —— 返回空 files + status="no_inquiry"
         的 shaped body，而不是 4xx。前端就能渲染"还没生成"。
      C. 有 inquiry, 多供应商 —— files[] 每个供应商一条 + supplier_count
         匹配 + status 透传。
      D. excel_file_url 提供时, filename 自动派生 (rsplit "/"[-1])
         —— 这是前端列表展示需要的。
      E. 失败的 supplier 也出现在 files[] 但 download_url 为 null +
         error_message 透传 —— 用户能看到哪个失败了。

为什么重要:
    这个 endpoint 是 frontend artifact panel 唯一的数据源。一旦契约漂,
    用户的"已生成 5 份询价单"卡片就空白。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from domains.identity.models import User
from domains.inquiry.models import Inquiry, InquirySupplier
from domains.orders.models import Order
from test_v2.fixtures.helpers import login


# ─── Helpers ────────────────────────────────────────────────


def _seed_order(
    session_factory: sessionmaker,
    *,
    user_id: int,
    po_number: str = "PO-TEST-001",
) -> int:
    db = session_factory()
    try:
        order = Order(
            user_id=user_id,
            filename="test.pdf",
            file_type="pdf",
            po_number=po_number,
            status="processed",
        )
        db.add(order)
        db.commit()
        return order.id
    finally:
        db.close()


def _seed_inquiry(
    session_factory: sessionmaker,
    *,
    order_id: int,
    suppliers: list[dict[str, Any]],
) -> None:
    """suppliers items: {supplier_id, supplier_name, excel_file_url?, status?, error_message?}"""
    db = session_factory()
    try:
        inquiry = Inquiry(
            order_id=order_id,
            status="succeeded",
            supplier_count=len(suppliers),
        )
        db.add(inquiry)
        db.flush()
        for s in suppliers:
            db.add(
                InquirySupplier(
                    inquiry_id=inquiry.id,
                    supplier_id=s["supplier_id"],
                    supplier_name=s.get("supplier_name", f"Supplier {s['supplier_id']}"),
                    excel_file_url=s.get("excel_file_url"),
                    status=s.get("status", "succeeded"),
                    error_message=s.get("error_message"),
                )
            )
        db.commit()
    finally:
        db.close()


# ─── A. cross-user isolation ────────────────────────────────


def test_other_users_order_returns_404(
    client: TestClient, session_factory: sessionmaker, seed_user: User
) -> None:
    """An attacker's request for a foreign order must 404, not 403 — never
    confirm the order's existence to non-owners."""
    other_id = _seed_order(session_factory, user_id=seed_user.id + 9999)
    headers = login(client, seed_user.email)
    res = client.get(
        f"/api/artifacts/inquiry-batch/{other_id}",
        headers=headers,
    )
    assert res.status_code == 404


def test_unauthenticated_request_is_rejected(client: TestClient) -> None:
    """No Bearer token → FastAPI rejects before our handler runs. Either
    401 (missing scheme) or 403 (default for HTTPBearer) is acceptable;
    what matters is it's NOT 200 and the body never leaks."""
    res = client.get("/api/artifacts/inquiry-batch/1")
    assert res.status_code in {401, 403}


# ─── B. order without inquiry ───────────────────────────────


def test_order_without_inquiry_returns_empty_shape(
    client: TestClient, session_factory: sessionmaker, seed_user: User
) -> None:
    order_id = _seed_order(session_factory, user_id=seed_user.id, po_number="PO-EMPTY")
    headers = login(client, seed_user.email)
    res = client.get(
        f"/api/artifacts/inquiry-batch/{order_id}",
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["order_id"] == order_id
    assert body["po_number"] == "PO-EMPTY"
    assert body["status"] == "no_inquiry"
    assert body["supplier_count"] == 0
    assert body["files"] == []


# ─── C. happy path: multiple suppliers ──────────────────────


def test_inquiry_with_multiple_files(
    client: TestClient, session_factory: sessionmaker, seed_user: User
) -> None:
    order_id = _seed_order(session_factory, user_id=seed_user.id, po_number="PO-OK")
    _seed_inquiry(
        session_factory,
        order_id=order_id,
        suppliers=[
            {
                "supplier_id": 11,
                "supplier_name": "Supplier A",
                "excel_file_url": "/api/files/inquiry_supplierA_PO-OK.xlsx",
                "status": "succeeded",
            },
            {
                "supplier_id": 12,
                "supplier_name": "Supplier B",
                "excel_file_url": "/api/files/inquiry_supplierB_PO-OK.xlsx",
                "status": "succeeded",
            },
        ],
    )
    headers = login(client, seed_user.email)
    res = client.get(
        f"/api/artifacts/inquiry-batch/{order_id}",
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["supplier_count"] == 2
    assert len(body["files"]) == 2
    by_id = {f["supplier_id"]: f for f in body["files"]}
    # D — filename derived from download URL last path component
    assert by_id[11]["filename"] == "inquiry_supplierA_PO-OK.xlsx"
    assert by_id[11]["download_url"] == "/api/files/inquiry_supplierA_PO-OK.xlsx"
    assert by_id[11]["status"] == "succeeded"
    assert by_id[11]["error_message"] is None


# ─── E. mixed success + failure ─────────────────────────────


def test_inquiry_with_failed_supplier(
    client: TestClient, session_factory: sessionmaker, seed_user: User
) -> None:
    order_id = _seed_order(session_factory, user_id=seed_user.id)
    _seed_inquiry(
        session_factory,
        order_id=order_id,
        suppliers=[
            {
                "supplier_id": 21,
                "supplier_name": "OK",
                "excel_file_url": "/api/files/ok.xlsx",
                "status": "succeeded",
            },
            {
                "supplier_id": 22,
                "supplier_name": "Broken",
                "excel_file_url": None,
                "status": "failed",
                "error_message": "Template not found",
            },
        ],
    )
    headers = login(client, seed_user.email)
    res = client.get(
        f"/api/artifacts/inquiry-batch/{order_id}",
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    by_id = {f["supplier_id"]: f for f in body["files"]}
    assert by_id[21]["download_url"] is not None
    assert by_id[22]["download_url"] is None
    assert by_id[22]["status"] == "failed"
    assert by_id[22]["error_message"] == "Template not found"
    # Failed supplier has no file URL → no filename derivation.
    assert by_id[22]["filename"] is None
