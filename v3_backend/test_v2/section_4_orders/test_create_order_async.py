"""Section 4 — Orders: async create-from-document (P0 2026-06-21).

为什么这套测试存在：
    Prod 用户报告"点击建单按钮卡住 30 秒才有反应"。根因是
    `create_from_document` 同步调 `run_matching`（含 Gemini LLM 调用，
    单次 8-30s），projection 内部还 *额外* 跑一次同样的 matching，HTTP
    请求挂住直到全部完成。修复方案：把 matching 推到 `infrastructure.jobs.runner`
    后台，endpoint 立刻返回 `status="matching"` 的 Order，前端订单页
    既有 2s 轮询自动接管。

    这套测试锁住这个契约。如果未来谁把 matching 推回同步链路（即
    create-order 响应再次包含已完成的匹配），第一条用例就红，作为
    UX 回归的硬门禁。

为什么用 monkeypatch run_matching：
    Gemini 真调用单测里无法控制时延 + 需要 API key。我们关心的是
    "endpoint 立刻返回 + 后台跑 + status 翻转"这条编排契约，而不是
    matcher 内部逻辑——后者由 section_4_orders/test_product_matching
    覆盖。
"""

from __future__ import annotations

from unittest.mock import patch

from domains.document.models import Document
from domains.orders.models import Order
from test_v2.fixtures.helpers import login, seed_user


# ─── Helpers ──────────────────────────────────────────────────


def _seed_extracted_po(db, *, user_id: int) -> Document:
    """A Document already past extraction, classified as purchase_order,
    with one product so projection has something to match against."""
    doc = Document(
        user_id=user_id,
        filename="po.pdf",
        file_type="pdf",
        file_size_bytes=1234,
        file_url="local/po.pdf",
        doc_type="purchase_order",
        status="extracted",
        extracted_data={
            "metadata": {
                "po_number": "PO-TEST-001",
                "ship_name": "Test Ship",
            },
            "products": [
                {"item_code": "X1", "description": "Apples", "quantity": 1},
            ],
        },
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


# ─── Endpoint contract — async (default) path ─────────────────


def test_create_order_returns_matching_status_without_running_gemini(
    client, db, session_factory
):
    """ASYNC_CREATE_ORDER=True (default): POST returns an Order whose
    status is "matching" — proving the request did NOT block on the
    Gemini matcher. The background job (SynchronousRunner under test
    config) then completes it, so by the time we re-fetch the order via
    a fresh session we see the post-job state.

    What we're locking in:
        (a) endpoint response status is "matching" — fast path is kept
        (b) projection didn't auto-match (run_match_inline=False)
        (c) the background task actually fires + writes
    """
    user = seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    doc = _seed_extracted_po(db, user_id=user.id)

    # Use a list captured by closure to count invocations. Mutating
    # `order.processing_error` from the fake would get clobbered by the
    # success-path's explicit `processing_error = None`; the call counter
    # survives independently and proves the background job actually
    # invoked our stub.
    inline_calls: list[int] = []
    background_calls: list[int] = []

    def stub_inline(order, db):
        # Projection's inline path — should NOT be called on async path.
        inline_calls.append(order.id)

    def stub_background(order, db):
        # Service's background path — must be called exactly once.
        background_calls.append(order.id)
        return {
            "statistics": {
                "total": 0,
                "matched": 0,
                "not_matched": 0,
                "match_rate": 0.0,
            }
        }

    with patch(
        "domains.orders.projection.run_matching", stub_inline
    ), patch("domains.orders.automation.run_matching", stub_background):
        r = client.post(
            f"/api/documents/{doc.id}/create-order",
            json={"force": False},
            headers=headers,
        )

    assert r.status_code == 200, r.text
    body = r.json()
    # Contract (a): endpoint's immediate response is the in-progress
    # state — that's the whole point of the async refactor.
    assert body["status"] == "matching", (
        "create-order endpoint must return status='matching' so the "
        "frontend can navigate immediately and let polling take over"
    )
    order_id = body["id"]

    # Contract (b): projection did NOT run the inline matcher.
    assert inline_calls == [], (
        "projection.run_matching should be skipped on the async path; "
        f"called for orders {inline_calls}"
    )
    # Contract (c): the background task did run the matcher exactly once.
    assert background_calls == [order_id], (
        "background task must invoke run_matching exactly once for the "
        f"new order; got {background_calls}"
    )

    # And the worker flipped the order to "ready". Use a fresh session
    # to bypass identity-map caching.
    fresh = session_factory()
    try:
        order = fresh.get(Order, order_id)
        assert order is not None
        assert order.status == "ready", (
            f"background job should have flipped status: got {order.status!r}"
        )
        assert order.processing_error is None  # success → cleared
    finally:
        fresh.close()


def test_create_order_records_processing_error_when_matching_raises(
    client, db, session_factory
):
    """匹配失败时，后台任务必须把 status 设成 'error' 并把异常文本写入
    processing_error，否则前端永远 polling 不到终态。Sister contract
    to the happy-path test above."""
    user = seed_user(db, email="bob@example.com", role="employee")
    headers = login(client, "bob@example.com")
    doc = _seed_extracted_po(db, user_id=user.id)

    def boom(order, db):
        raise RuntimeError("Gemini quota exceeded")

    with patch("domains.orders.automation.run_matching", boom), patch(
        "domains.orders.projection.run_matching", boom
    ):
        r = client.post(
            f"/api/documents/{doc.id}/create-order",
            json={"force": False},
            headers=headers,
        )
    assert r.status_code == 200, r.text
    order_id = r.json()["id"]

    fresh = session_factory()
    try:
        order = fresh.get(Order, order_id)
        assert order is not None
        assert order.status == "error"
        assert order.processing_error is not None
        assert "Gemini quota exceeded" in order.processing_error
    finally:
        fresh.close()


def test_create_order_force_flag_accepts_non_po_document(client, db):
    """force=True 路径：即便分类器没识别为 PO，用户点'这是订单'按钮
    强制建单仍能通过。Regression guard for the prod UX where the
    classifier sometimes misses a PO and the user has to override."""
    user = seed_user(db, email="carol@example.com", role="employee")
    headers = login(client, "carol@example.com")
    doc = Document(
        user_id=user.id,
        filename="ambiguous.pdf",
        file_type="pdf",
        file_size_bytes=500,
        file_url="local/x.pdf",
        doc_type="unknown",  # classifier missed it
        status="extracted",
        extracted_data={
            "metadata": {"po_number": "X"},
            "products": [{"item_code": "A", "description": "A", "quantity": 1}],
        },
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    with patch("domains.orders.service.run_matching", lambda o, d: None), patch(
        "domains.orders.projection.run_matching", lambda o, d: None
    ):
        # Without force → 400
        r = client.post(
            f"/api/documents/{doc.id}/create-order",
            json={"force": False},
            headers=headers,
        )
        assert r.status_code == 400

        # With force → 200 + status="matching"
        r = client.post(
            f"/api/documents/{doc.id}/create-order",
            json={"force": True},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "matching"


# ─── Sync fallback path (feature flag off) ────────────────────


def test_create_order_is_idempotent_on_double_submit(client, db, session_factory):
    """Prod incident 2026-06-22: a user double-clicked "确认建单",
    two requests slipped past the doc-type check, and the second
    `create_from_document` call created a second Order. Downstream
    `find_order_id_for_document` then crashed with `MultipleResultsFound`
    and the document list endpoint started returning 500 (which the
    browser surfaced as a misleading CORS error because the 500 escapes
    the CORS middleware without headers).

    Contract this test locks in: a second POST against the same document
    must return the EXISTING order, not create a new one. The fix also
    keeps stale-data prod state self-healing — old rows with duplicate
    document_id stop multiplying."""
    user = seed_user(db, email="dave2@example.com", role="employee")
    headers = login(client, "dave2@example.com")
    doc = _seed_extracted_po(db, user_id=user.id)

    with patch("domains.orders.service.run_matching", lambda o, d: None), patch(
        "domains.orders.projection.run_matching", lambda o, d: None
    ):
        r1 = client.post(
            f"/api/documents/{doc.id}/create-order",
            json={"force": False},
            headers=headers,
        )
        r2 = client.post(
            f"/api/documents/{doc.id}/create-order",
            json={"force": False},
            headers=headers,
        )

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["id"] == r2.json()["id"], (
        "double-submit must return the SAME order, not create a duplicate"
    )

    # Verify DB has exactly one order for this doc — proves no stale rows
    # are silently leaking.
    fresh = session_factory()
    try:
        count = (
            fresh.query(Order).filter(Order.document_id == doc.id).count()
        )
        assert count == 1, f"expected 1 order per document, got {count}"
    finally:
        fresh.close()


def test_create_order_sync_fallback_when_flag_off(client, db, session_factory):
    """`ASYNC_CREATE_ORDER=False` must restore the legacy sync behavior
    so we have a 1-line revert if the polling UI ever regresses. The
    endpoint should return with status already "ready" (or "error"),
    NOT "matching"."""
    user = seed_user(db, email="dave@example.com", role="employee")
    headers = login(client, "dave@example.com")
    doc = _seed_extracted_po(db, user_id=user.id)

    from infrastructure.config import settings

    with patch.object(settings, "ASYNC_CREATE_ORDER", False), patch(
        "domains.orders.service.run_matching", lambda o, d: None
    ), patch("domains.orders.projection.run_matching", lambda o, d: None):
        r = client.post(
            f"/api/documents/{doc.id}/create-order",
            json={"force": False},
            headers=headers,
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready", (
        f"sync fallback must return terminal state, got {body['status']!r}"
    )
