"""Section 4 — Orders: Document → Order 投影 (`domains/orders/projection.py`).

测试目标:
    `project_purchase_order(document, db)` 是把通用 Document JSON 转成 Order
    行的关键一步。它必须:
      1. 把 `extracted_data.metadata` 里的 po_number/ship_name/currency/...
         **提升成 Order 的列字段** (不再藏在 JSON 里)。
      2. **幂等** — 同一个 document 重复调用不会产生第二条 Order 行。
      3. 在 `extracted_data.products` 存在时, 调用匹配并把结果存进
         `match_results` / `match_statistics`, 状态从 `extracted` 升到 `ready`。
      4. **错误隔离** — 即使匹配 raise 了, Order 行也必须先于匹配持久化, 错误
         走 `processing_error`, 状态停在 `extracted` (可重试)。
      5. 缺少可选字段不能崩 (没有 metadata、没有 products、没有 file_url 都不能崩)。

为什么重要:
    这一步是 v2→v3 数据迁移的物理边界。投影错了, 所有下游 (匹配、询价、报表)
    全部错。幂等性尤其关键 — Document 的重抽取或 user 手动重跑都会触发投影,
    它必须能多次跑安全。

设计方法:
    - 用真 SQLite 在 conftest 的 `db` fixture 上跑, 不 mock SQLAlchemy。
    - 用 monkeypatch 把 LLM 抽取器禁用 (默认 conftest 已设 GOOGLE_API_KEY="")
      或换成 None 返回, 这样测试不走 Gemini。
    - 用 monkeypatch 替换 `run_matching` 来精确控制匹配结果 (含失败路径)。
    - 直接构造 Document ORM 行喂给投影函数, 不走 upload API (那是 Section 3)。
"""

from __future__ import annotations

from typing import Any

import pytest

from domains.document.models import Document
from domains.orders import projection, repository
from domains.orders.models import Order
from test_v2.fixtures.helpers import seed_user


# ─── Helpers ──────────────────────────────────────────────────


def _make_doc(
    db,
    *,
    user_id: int,
    metadata: dict[str, Any] | None = None,
    products: list[dict[str, Any]] | None = None,
    markdown: str = "",
    file_url: str | None = None,
    file_type: str = "pdf",
) -> Document:
    """Insert a minimal Document row representing already-extracted content."""
    extracted: dict[str, Any] = {}
    if metadata is not None:
        extracted["metadata"] = metadata
    if products is not None:
        extracted["products"] = products
    if markdown:
        extracted["markdown"] = markdown
    doc = Document(
        user_id=user_id,
        filename="test.pdf",
        file_type=file_type,
        file_url=file_url,
        doc_type="purchase_order",
        extracted_data=extracted or None,
        content_markdown=markdown or None,
        status="extracted",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


@pytest.fixture
def stub_run_matching(monkeypatch):
    """Patch run_matching so it doesn't touch masterdata or external services.

    Tests that care about *what* matching did override this fixture's behavior
    via the returned recorder.
    """
    calls: list[tuple[Order, Any]] = []

    def _fake(order: Order, db) -> dict[str, Any]:
        calls.append((order, db))
        order.match_results = [{"product_code": "X", "match_status": "matched"}]
        order.match_statistics = {"total": 1, "matched": 1, "not_matched": 0, "match_rate": 100.0}
        return {"match_results": order.match_results, "statistics": order.match_statistics}

    monkeypatch.setattr(projection, "run_matching", _fake)
    return calls


@pytest.fixture
def disable_llm_enrich(monkeypatch):
    """Force the LLM enrichment step to return None (no network)."""
    monkeypatch.setattr(projection, "_llm_extract_po", lambda *a, **kw: None)


@pytest.fixture
def user(db):
    return seed_user(db, email="po-projection@example.com")


# ─── metadata → columns promotion ─────────────────────────────


def test_promotes_metadata_to_columns(db, user, stub_run_matching, disable_llm_enrich):
    """metadata JSON 里的 PO 字段必须升到 Order 行对应的列。"""
    doc = _make_doc(
        db,
        user_id=user.id,
        metadata={
            "po_number": "PO-12345",
            "ship_name": "VOYAGER",
            "vendor_name": "Acme Foods",
            "currency": "USD",
            "delivery_date": "2026-05-20",
            "order_date": "2026-05-01",
            "destination_port": "Tokyo",
            "total_amount": "1500.50",
        },
        products=[{"product_code": "A1", "product_name": "Apple", "quantity": 10}],
    )

    order = projection.project_purchase_order(doc, db)
    db.refresh(order)

    assert order.po_number == "PO-12345"
    assert order.ship_name == "VOYAGER"
    assert order.vendor_name == "Acme Foods"
    assert order.currency == "USD"
    assert order.delivery_date == "2026-05-20"
    assert order.order_date == "2026-05-01"
    assert order.destination_port == "Tokyo"
    assert float(order.total_amount) == 1500.50


def test_field_evidence_records_source(db, user, stub_run_matching, disable_llm_enrich):
    """每个被提升的字段必须在 field_evidence 里有 source 记录, 方便审计."""
    doc = _make_doc(
        db,
        user_id=user.id,
        metadata={"po_number": "PO-9", "ship_name": "TITANIC"},
        products=[],
    )
    order = projection.project_purchase_order(doc, db)

    assert isinstance(order.field_evidence, dict)
    assert "po_number" in order.field_evidence
    assert order.field_evidence["po_number"]["source"] == "extracted_data.metadata"
    assert order.field_evidence["po_number"]["value"] == "PO-9"


# ─── Idempotency ──────────────────────────────────────────────


def test_idempotent_no_duplicate_order_rows(db, user, stub_run_matching, disable_llm_enrich):
    """对同一个 document 调用两次, 只产生一条 Order 行 (update, not insert)."""
    doc = _make_doc(
        db,
        user_id=user.id,
        metadata={"po_number": "PO-DUP"},
        products=[],
    )

    o1 = projection.project_purchase_order(doc, db)
    o2 = projection.project_purchase_order(doc, db)

    assert o1.id == o2.id
    # 数据库里只有一条
    all_orders = db.query(Order).filter(Order.document_id == doc.id).all()
    assert len(all_orders) == 1


def test_idempotent_updates_fields_on_rerun(db, user, stub_run_matching, disable_llm_enrich):
    """第二次跑如果 metadata 变了, Order 的列也跟着变 — 不是只更新一次就锁死."""
    doc = _make_doc(
        db,
        user_id=user.id,
        metadata={"po_number": "PO-OLD"},
        products=[],
    )
    projection.project_purchase_order(doc, db)

    # 改 metadata 模拟用户重抽取了
    doc.extracted_data = {"metadata": {"po_number": "PO-NEW"}, "products": []}
    db.commit()

    order = projection.project_purchase_order(doc, db)
    db.refresh(order)
    assert order.po_number == "PO-NEW"


# ─── Products & match wiring ──────────────────────────────────


def test_products_persisted_and_count_set(db, user, stub_run_matching, disable_llm_enrich):
    """products 列表必须被复制到 Order.products, 且 product_count 计数正确."""
    products = [
        {"product_code": "P1", "product_name": "Foo", "quantity": 1},
        {"product_code": "P2", "product_name": "Bar", "quantity": 2},
        {"product_code": "P3", "product_name": "Baz", "quantity": 3},
    ]
    doc = _make_doc(db, user_id=user.id, metadata={}, products=products)

    order = projection.project_purchase_order(doc, db)

    assert order.product_count == 3
    assert isinstance(order.products, list)
    assert {p["product_code"] for p in order.products} == {"P1", "P2", "P3"}


def test_matching_runs_when_products_present(db, user, stub_run_matching, disable_llm_enrich):
    """有 products → 调匹配 → status 升到 ready, processing_error 清空."""
    doc = _make_doc(
        db,
        user_id=user.id,
        metadata={"po_number": "PO-1"},
        products=[{"product_code": "P1", "product_name": "X", "quantity": 1}],
    )

    order = projection.project_purchase_order(doc, db)

    assert len(stub_run_matching) == 1, "run_matching should have been called once"
    assert order.status == "ready"
    assert order.processing_error is None
    assert order.match_statistics == {
        "total": 1,
        "matched": 1,
        "not_matched": 0,
        "match_rate": 100.0,
    }


def test_no_matching_when_no_products(db, user, stub_run_matching, disable_llm_enrich):
    """没有 products → 不调匹配, status 停在 extracted."""
    doc = _make_doc(db, user_id=user.id, metadata={"po_number": "PO-NOLINES"}, products=[])

    order = projection.project_purchase_order(doc, db)

    assert len(stub_run_matching) == 0, "run_matching should be skipped when no products"
    assert order.status == "extracted"


# ─── Error resilience: matching failure ───────────────────────


def test_matching_failure_does_not_destroy_order(db, user, monkeypatch, disable_llm_enrich):
    """匹配 raise 异常时, Order 必须已经持久化 (column 字段都在), 错误记到
    processing_error, status 停在 extracted (而不是 ready) 以便用户重试。"""

    def _exploding_match(order, db):
        raise RuntimeError("simulated matching crash")

    monkeypatch.setattr(projection, "run_matching", _exploding_match)

    doc = _make_doc(
        db,
        user_id=user.id,
        metadata={"po_number": "PO-CRASH", "ship_name": "FAILBOAT"},
        products=[{"product_code": "X", "product_name": "Y", "quantity": 1}],
    )

    order = projection.project_purchase_order(doc, db)
    db.refresh(order)

    # 字段还在
    assert order.po_number == "PO-CRASH"
    assert order.ship_name == "FAILBOAT"
    # 错误被捕获并记录
    assert order.processing_error is not None
    assert "simulated matching crash" in order.processing_error
    # 状态停在 extracted (不能错误地跳到 ready)
    assert order.status == "extracted"


# ─── Error resilience: LLM enrichment ─────────────────────────


def test_no_llm_call_when_products_already_present(db, user, stub_run_matching, monkeypatch):
    """已经有 products 就不要再 call LLM 浪费配额."""
    call_count = {"n": 0}

    def _spy(*a, **kw):
        call_count["n"] += 1
        return None

    monkeypatch.setattr(projection, "_llm_extract_po", _spy)

    doc = _make_doc(
        db,
        user_id=user.id,
        metadata={"po_number": "PO-X"},
        products=[{"product_code": "A", "product_name": "B", "quantity": 1}],
    )
    projection.project_purchase_order(doc, db)

    assert call_count["n"] == 0


def test_no_llm_call_when_file_url_missing(db, user, stub_run_matching, monkeypatch):
    """`file_url is None` → 无法下载 raw 字节 → 跳过 LLM (gracefully)."""
    call_count = {"n": 0}

    def _spy(*a, **kw):
        call_count["n"] += 1
        return None

    monkeypatch.setattr(projection, "_llm_extract_po", _spy)

    doc = _make_doc(db, user_id=user.id, metadata={}, products=[], file_url=None)
    projection.project_purchase_order(doc, db)

    assert call_count["n"] == 0


def test_no_llm_call_when_unsupported_file_type(db, user, stub_run_matching, monkeypatch):
    """`.txt` 不是 LLM 抽取器支持的类型 — 跳过, 不能崩."""
    call_count = {"n": 0}
    monkeypatch.setattr(
        projection, "_llm_extract_po", lambda *a, **kw: call_count.__setitem__("n", call_count["n"] + 1) or None
    )

    doc = _make_doc(
        db, user_id=user.id, metadata={}, products=[], file_type="txt", file_url="some/path.txt"
    )
    projection.project_purchase_order(doc, db)

    assert call_count["n"] == 0


def test_llm_enrichment_merges_into_extracted_data(
    db, user, stub_run_matching, monkeypatch, _local_storage
):
    """LLM 返回 metadata + products 时, 它们应当被 merge 回 document.extracted_data
    以及 Order.products, 让下游能看到。"""
    # 让 LLM 返回成功
    monkeypatch.setattr(
        projection,
        "_llm_extract_po",
        lambda *a, **kw: {
            "order_metadata": {"po_number": "LLM-PO-1", "currency": "JPY"},
            "products": [{"product_code": "LLMP1", "product_name": "LLM Product", "quantity": 5}],
        },
    )
    # 上传一个假文件让 storage.download 不爆 (LocalFileStorage 会 download 这个 url)
    storage_key = _local_storage.upload(
        folder="docs", filename="fake.pdf", content=b"%PDF-1.4 fake"
    )

    doc = _make_doc(
        db,
        user_id=user.id,
        metadata={},
        products=None,  # 触发 LLM 路径
        file_url=storage_key,
        file_type="pdf",
    )

    order = projection.project_purchase_order(doc, db)
    db.refresh(doc)

    # LLM 结果被合并回 document
    assert doc.extracted_data["metadata"]["po_number"] == "LLM-PO-1"
    # Order 列也被提升了
    assert order.po_number == "LLM-PO-1"
    assert order.currency == "JPY"
    # 产品被持久化
    assert order.product_count == 1
    assert order.products[0]["product_code"] == "LLMP1"


def test_llm_extraction_failure_does_not_break_projection(
    db, user, stub_run_matching, monkeypatch, _local_storage
):
    """LLM raise ExtractorError 时, 投影必须继续, 错误走 document.processing_error。"""
    from domains.orders._llm_extractor import ExtractorError

    def _raise(*a, **kw):
        raise ExtractorError("simulated gemini failure")

    monkeypatch.setattr(projection, "_llm_extract_po", _raise)
    storage_key = _local_storage.upload(
        folder="docs", filename="fake.pdf", content=b"%PDF-1.4 fake"
    )

    doc = _make_doc(
        db,
        user_id=user.id,
        metadata={"po_number": "PO-METADATA-STILL-HERE"},
        products=None,
        file_url=storage_key,
        file_type="pdf",
    )

    order = projection.project_purchase_order(doc, db)
    db.refresh(doc)

    # Order 还在, 已知字段仍然被提升
    assert order is not None
    assert order.po_number == "PO-METADATA-STILL-HERE"
    # 错误被记到 document 而非传染到 order
    assert doc.processing_error is not None
    assert "simulated gemini failure" in doc.processing_error


# ─── Regex fallback ───────────────────────────────────────────


def test_regex_fallback_extracts_po_from_markdown(db, user, stub_run_matching, disable_llm_enrich):
    """metadata 里没有 po_number 时, 走 markdown 正则 fallback."""
    doc = _make_doc(
        db,
        user_id=user.id,
        metadata={},  # no po_number in metadata
        products=[],
        markdown="Header\nPurchase Order PO-TEST-42\nMore text",
    )

    order = projection.project_purchase_order(doc, db)
    # 正则模式 `PO-TEST-42` 被 `_PO_PATTERNS` 第一个捕获
    assert order.po_number is not None
    assert "TEST-42" in order.po_number


def test_regex_fallback_detects_currency(db, user, stub_run_matching, disable_llm_enrich):
    """三字母货币码出现在 markdown 里, 应当被识别."""
    doc = _make_doc(
        db,
        user_id=user.id,
        metadata={},
        products=[],
        markdown="Invoice total payable in JPY for the goods.",
    )
    order = projection.project_purchase_order(doc, db)
    assert order.currency == "JPY"


# ─── Linkage preservation ─────────────────────────────────────


def test_order_links_back_to_document(db, user, stub_run_matching, disable_llm_enrich):
    """Order.document_id 必须正确指回 Document; repository.get_by_document_id
    应当能反向查到。"""
    doc = _make_doc(db, user_id=user.id, metadata={"po_number": "PO-LINK"}, products=[])
    order = projection.project_purchase_order(doc, db)

    assert order.document_id == doc.id
    fetched = repository.get_by_document_id(db, doc.id)
    assert fetched is not None
    assert fetched.id == order.id


def test_file_metadata_kept_in_sync_on_rerun(db, user, stub_run_matching, disable_llm_enrich):
    """如果 user 重新上传同 doc_id 的不同文件, 投影应当用 Document 最新的
    file_url/filename 更新 Order (不是死锁老值)."""
    doc = _make_doc(
        db, user_id=user.id, metadata={"po_number": "PO-X"}, products=[], file_url="old/path.pdf"
    )
    projection.project_purchase_order(doc, db)

    doc.file_url = "new/path.pdf"
    doc.filename = "new.pdf"
    db.commit()

    order = projection.project_purchase_order(doc, db)
    db.refresh(order)
    assert order.file_url == "new/path.pdf"
    assert order.filename == "new.pdf"
