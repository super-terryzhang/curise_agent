"""Section 3 — Documents: async doc-type change (2026-06-22).

为什么这套测试存在：
    Prod 用户上传文档后点"这是订单"按钮，UI 卡住 30 秒以上才反应。
    根因：`update_doc_type` 同步调 `enrich_purchase_order_document`
    （含 Gemini Vision，单次 8-30s），HTTP 请求挂住直到全部完成；
    经常超过浏览器 / Cloud Run 的请求超时上限直接失败。

    修复方案：复用 P0 create-order 的异步模式 —— endpoint 立刻把
    `status="extracting"` 落库后返回，背景 job 跑富化，前端原有的
    详情页 2s 轮询自动接管。

    这套测试锁住这条契约。任何把富化推回同步链路的改动（例如
    "请求一定要含产品列表"这种想法）会在第一条用例上红，作为 UX
    回归的硬门禁。

为什么 monkeypatch enrich_purchase_order_document：
    Gemini 真调用单测无法控制时延 + 需要 API key。我们关心的是
    "endpoint 立刻返回 + 后台跑 + status 翻转"这条编排契约，富化
    内部已由 `enrichment.py` 单测覆盖。
"""

from __future__ import annotations

from unittest.mock import patch

from domains.document.models import Document
from test_v2.fixtures.helpers import login, seed_user


# ─── Helpers ──────────────────────────────────────────────────


def _seed_unknown_doc(db, *, user_id: int) -> Document:
    """A Document past extraction, classified as `unknown` — the state
    a user is in when they click "这是订单" to override the classifier."""
    doc = Document(
        user_id=user_id,
        filename="ambiguous.pdf",
        file_type="pdf",
        file_size_bytes=1234,
        file_url="local/ambiguous.pdf",
        doc_type="unknown",
        status="extracted",
        extracted_data={"metadata": {}},
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


# ─── Endpoint contract — async (default) path ─────────────────


def test_doc_type_change_returns_extracting_without_running_gemini(
    client, db, session_factory
):
    """ASYNC_DOC_TYPE_ENRICH=True (default): PATCH returns a doc whose
    status is "extracting" — proving the request did NOT block on Gemini.
    The background job (SynchronousRunner under test config) then writes
    products + flips status; we re-fetch via a fresh session to see it.

    Contract:
        (a) endpoint response status is "extracting" — fast path is kept
        (b) the background task actually fires + invokes enrichment exactly once
        (c) post-job status flips to "extracted" (terminal success)
    """
    user = seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    doc = _seed_unknown_doc(db, user_id=user.id)

    enrich_calls: list[int] = []

    def stub_enrich(document):
        enrich_calls.append(document.id)
        # Simulate the real enrich function mutating the doc — adding a
        # product so the post-job state has something to assert against.
        data = dict(document.extracted_data or {})
        data["products"] = [{"item_code": "X1", "quantity": 1}]
        document.extracted_data = data

    # Patch the package-level re-export. Both `update_doc_type` and
    # `_run_doc_type_enrichment_sync` import via
    # `from domains.orders import enrich_purchase_order_document`, so
    # the binding to patch is `domains.orders.enrich_purchase_order_document`
    # (the package namespace), NOT the source module.
    with patch(
        "domains.orders.enrich_purchase_order_document",
        stub_enrich,
    ):
        r = client.patch(
            f"/api/documents/{doc.id}",
            json={"doc_type": "purchase_order"},
            headers=headers,
        )

    assert r.status_code == 200, r.text
    body = r.json()
    # Contract (a): endpoint's immediate response is the in-progress state.
    assert body["status"] == "extracting", (
        "PATCH /documents/{id} must return status='extracting' so the "
        "frontend polling can take over; got %r" % body["status"]
    )
    assert body["doc_type"] == "purchase_order"

    # Contract (b): background task ran enrichment exactly once.
    assert enrich_calls == [doc.id], (
        "background job must invoke enrich exactly once; got %r" % enrich_calls
    )

    # Contract (c): worker flipped doc to "extracted" + persisted products.
    fresh = session_factory()
    try:
        reloaded = fresh.get(Document, doc.id)
        assert reloaded is not None
        assert reloaded.status == "extracted", (
            "background job should flip status to extracted: got %r"
            % reloaded.status
        )
        assert reloaded.processing_error is None
        products = (reloaded.extracted_data or {}).get("products") or []
        assert products and products[0]["item_code"] == "X1"
    finally:
        fresh.close()


def test_doc_type_change_records_error_when_enrich_raises(
    client, db, session_factory
):
    """富化抛异常时，后台任务必须把 status 设成 'error' 并把异常
    文本写入 processing_error，否则前端永远 polling 不到终态。"""
    user = seed_user(db, email="bob@example.com", role="employee")
    headers = login(client, "bob@example.com")
    doc = _seed_unknown_doc(db, user_id=user.id)

    def boom(document):
        raise RuntimeError("Gemini quota exceeded")

    with patch(
        "domains.orders.enrich_purchase_order_document",
        boom,
    ):
        r = client.patch(
            f"/api/documents/{doc.id}",
            json={"doc_type": "purchase_order"},
            headers=headers,
        )
    assert r.status_code == 200, r.text

    fresh = session_factory()
    try:
        reloaded = fresh.get(Document, doc.id)
        assert reloaded is not None
        assert reloaded.status == "error"
        assert reloaded.processing_error is not None
        assert "Gemini quota exceeded" in reloaded.processing_error
    finally:
        fresh.close()


def test_doc_type_change_to_non_po_does_not_trigger_enrich(client, db):
    """切换到 invoice / quote / unknown 不应该触发富化 —— 富化只在
    "用户认定这是 PO"时跑。Sister guard: 也不应该把 status 翻成
    extracting（请求是即时完成的）。"""
    user = seed_user(db, email="carol@example.com", role="employee")
    headers = login(client, "carol@example.com")
    doc = _seed_unknown_doc(db, user_id=user.id)

    enrich_calls: list[int] = []

    def stub_enrich(document):
        enrich_calls.append(document.id)

    with patch(
        "domains.orders.enrich_purchase_order_document",
        stub_enrich,
    ):
        r = client.patch(
            f"/api/documents/{doc.id}",
            json={"doc_type": "invoice"},
            headers=headers,
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["doc_type"] == "invoice"
    # Status should NOT become extracting — nothing's running.
    assert body["status"] == "extracted"
    assert enrich_calls == [], (
        "enrich should only fire when transitioning TO purchase_order; "
        "got calls %r" % enrich_calls
    )


# ─── Sync fallback path (feature flag off) ────────────────────


def test_doc_type_change_sync_fallback_when_flag_off(client, db):
    """`ASYNC_DOC_TYPE_ENRICH=False` 必须恢复 legacy 同步行为 ——
    1-line revert if the polling UI ever regresses. 在同步路径下
    endpoint 必须挂到富化完成再返回。"""
    user = seed_user(db, email="dave@example.com", role="employee")
    headers = login(client, "dave@example.com")
    doc = _seed_unknown_doc(db, user_id=user.id)

    enrich_calls: list[int] = []

    def stub_enrich(document):
        enrich_calls.append(document.id)
        data = dict(document.extracted_data or {})
        data["products"] = [{"item_code": "Y1", "quantity": 2}]
        document.extracted_data = data

    from infrastructure.config import settings

    with patch.object(settings, "ASYNC_DOC_TYPE_ENRICH", False), patch(
        "domains.orders.enrich_purchase_order_document",
        stub_enrich,
    ):
        r = client.patch(
            f"/api/documents/{doc.id}",
            json={"doc_type": "purchase_order"},
            headers=headers,
        )

    assert r.status_code == 200, r.text
    body = r.json()
    # Sync path: response already carries the enriched products + extracted state.
    assert body["status"] == "extracted", (
        "sync fallback must return terminal state, got %r" % body["status"]
    )
    assert enrich_calls == [doc.id]
    # Products from the stubbed enrich are projected in extracted_data.
    products = (body.get("extracted_data") or {}).get("products") or []
    assert products and products[0]["item_code"] == "Y1"
