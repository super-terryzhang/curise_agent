"""Section E2E — Real PDF upload pipeline.

测试目标：
    用 test-orders/ 里的 7 个真实邮轮订单 PDF，验证 POST /api/orders/upload
    端到端管线能跑完。具体不验证 LLM 提取的字段（那需要真 Gemini + 成本）；
    只验证 file storage / document creation / classifier / order projection
    这些 LLM-independent 的环节能完整跑通真实文件。

为什么重要：
    这是用户日常的核心操作。如果某个文件格式 / 大小让管线挂掉，用户就完全
    没法用系统。E2E 用真文件覆盖合成 PDF 测不到的边缘情况（压缩、嵌入对象、
    Excel 转 PDF、扫描页等）。

设计方法：
    每个真 PDF 一个测试。upload → 拿到 Document/Order 行 → 断言 status
    和基本字段。LLM 字段 (po_number / ship_name 等) 在没 API key 时为
    None，这是预期；E2E 不测提取质量，只测管线不崩。
"""

from __future__ import annotations

import pytest


# ─── 工具：上传 PDF + 验证 Order 创建 ─────────────────────────


def _upload_pdf(client, auth_headers, name: str, blob: bytes) -> dict:
    """Upload a PDF blob and return the JSON response. Asserts 2xx."""
    r = client.post(
        "/api/orders/upload",
        files={"file": (name, blob, "application/pdf")},
        headers=auth_headers,
    )
    assert r.status_code == 200, f"upload failed for {name}: {r.status_code} {r.text[:200]}"
    return r.json()


# ─── 1. 各 PDF 单独上传：管线不崩 ─────────────────────────────


@pytest.mark.parametrize(
    "sample_name",
    [
        "tiny_po_68007850",
        "small_po_68331111",
        "small_po_68358749",
        "cci_po_107309",
        "silver_nova",
        "excel_pdf_cyi",
    ],
)
def test_upload_real_pdf_creates_order(
    client, employee_auth, sample_pdfs, sample_name
):
    """Each real PDF (except the giant 25MB one) uploads → Order row created."""
    blob = sample_pdfs[sample_name]
    order = _upload_pdf(client, employee_auth, f"{sample_name}.pdf", blob)

    assert order["id"] > 0
    # Status is whatever the pipeline reached — could be uploading, extracted,
    # or ready_for_review. The point is it's not crashed (HTTP 500).
    assert order["status"] in {
        "uploading",
        "extracted",
            "ready_for_review",
            "classified",
            "ready",
    }, f"unexpected status {order['status']!r}"
    assert order["filename"] == f"{sample_name}.pdf"
    assert order["file_type"] in {"pdf", "PDF", None}


def test_upload_large_pdf_does_not_timeout(client, employee_auth, sample_pdfs):
    """The 25MB compressed CCI PDF must process without hitting any size limit
    or timeout. If it does, real cruise users with similar files are blocked."""
    blob = sample_pdfs["cci_large_po_102292"]
    assert len(blob) > 20_000_000, "expected the large sample to be > 20MB"
    order = _upload_pdf(client, employee_auth, "cci_large.pdf", blob)
    assert order["id"] > 0


# ─── 2. Document 行也被创建 + 内容真的提取了 ─────────────────


def test_upload_creates_document_row_with_extracted_content(
    client, employee_auth, sample_pdfs, db
):
    """Upload should produce a Document row with content_markdown filled in
    by pypdfium2 (no LLM needed for born-digital PDFs)."""
    from domains.document.models import Document

    blob = sample_pdfs["small_po_68331111"]
    order = _upload_pdf(client, employee_auth, "68331111.pdf", blob)

    doc = db.get(Document, order["document_id"])
    assert doc is not None
    # Born-digital PDFs should produce non-empty markdown via pypdfium2
    # regardless of LLM availability.
    assert doc.content_markdown, "Document.content_markdown should be populated"
    assert len(doc.content_markdown) > 50, "extracted markdown suspiciously short"


# ─── 3. 同一用户能 list 自己的所有上传 ────────────────────────


def test_uploaded_orders_appear_in_list(client, employee_auth, sample_pdfs):
    """After uploading 3 PDFs, GET /api/orders should show all 3."""
    names = ["tiny_po_68007850", "small_po_68331111", "small_po_68358749"]
    uploaded_ids = []
    for name in names:
        order = _upload_pdf(client, employee_auth, f"{name}.pdf", sample_pdfs[name])
        uploaded_ids.append(order["id"])

    r = client.get("/api/orders", headers=employee_auth)
    assert r.status_code == 200
    listed_ids = {item["id"] for item in r.json()["items"]}
    assert set(uploaded_ids).issubset(listed_ids)


# ─── 4. 跨用户隔离：另一员工看不到 ──────────────────────────


def test_other_user_cannot_see_uploaded_order(
    client, employee_auth, sample_pdfs, db
):
    """User A uploads, User B's GET /api/orders does not show it."""
    from test_v2.fixtures.helpers import login, seed_user

    order = _upload_pdf(
        client, employee_auth, "private.pdf", sample_pdfs["tiny_po_68007850"]
    )

    seed_user(db, email="other_e2e@x.test", role="employee", password="password123")
    other_auth = login(client, "other_e2e@x.test")
    r = client.get("/api/orders", headers=other_auth)
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()["items"]}
    assert order["id"] not in ids


# ─── 5. 详情页能拿到原始文件 ─────────────────────────────────


def test_uploaded_file_can_be_downloaded(client, employee_auth, sample_pdfs):
    """After upload, GET /orders/{id}/file-preview returns a working URL."""
    order = _upload_pdf(
        client, employee_auth, "x.pdf", sample_pdfs["small_po_68358749"]
    )
    r = client.get(
        f"/api/orders/{order['id']}/file-preview", headers=employee_auth
    )
    # Endpoint exists and returns either a redirect or signed URL JSON.
    assert r.status_code in {200, 302, 307}
