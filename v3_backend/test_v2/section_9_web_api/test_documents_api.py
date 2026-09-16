"""Section 9 — Web API: /api/documents/* HTTP contract.

测试目标：
    `apps/http/documents.py` 是上传 + 列表 + 详情 + 用户标签 + order
    payload + 创建订单 + 文件流的 12 个端点的薄壳。这里走 conftest 的
    SynchronousRunner + LocalFileStorage 跑完整 PDF/XLSX 上传 pipeline，
    断言：HTTP status、payload shape、DB state、跨用户隔离。

为什么重要：
    - 上传完成后状态必须可观察（status="extracted" + extraction_method）。
      否则前端永远轮询「正在处理」。
    - 文档默认属于上传者；admin 才能看别人的。404 vs 403 的区别决定了
      会不会泄露"文档存在性"——错的话就成了 enumeration 漏洞。
    - create-order 接口需要保护：非 PO 文档不能误生成订单，除非 force=True。

设计方法：
    - 用 `make_minimal_pdf` / `make_excel` 合成最小可上传文件。
    - 一份测试一个端点；多个 PATCH/POST/DELETE 串成一个工作流的，分开
      写但相互引用 helper（_upload_pdf）。
    - 跨用户隔离：用 helpers.seed_user 注两个 employee，分别登录，验证
      A 的 token 拿不到 B 的文档（404 而不是 403）。
"""

from __future__ import annotations

from domains.document.models import Document
from test_v2.fixtures.helpers import login, make_excel, make_minimal_pdf, seed_user


# ─── Helpers ──────────────────────────────────────────────────


def _upload_pdf(client, headers, filename: str = "test.pdf") -> dict:
    """Upload `make_minimal_pdf()` and return the response body."""
    pdf_bytes = make_minimal_pdf("hello")
    r = client.post(
        "/api/documents/upload",
        files={"file": (filename, pdf_bytes, "application/pdf")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


# ─── POST /upload ─────────────────────────────────────────────


def test_upload_pdf_runs_pipeline_to_extracted(client, db, session_factory):
    """PDF 上传 → SynchronousRunner 跑完 workflow → 状态终态 extracted。
    extraction_method 必须填 PyPDFium 的名字（不能是 None / 空字符串）。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")

    body = _upload_pdf(client, headers, filename="invoice.pdf")
    assert body["filename"] == "invoice.pdf"
    assert body["file_type"] == "pdf"
    assert body["status"] == "extracted"
    assert body["extraction_method"] == "pypdfium2"

    # DB confirms it.
    fresh = session_factory()
    try:
        doc = fresh.query(Document).filter(Document.id == body["id"]).one()
        assert doc.status == "extracted"
        assert doc.extraction_method == "pypdfium2"
        assert doc.file_url  # storage key was written
    finally:
        fresh.close()


def test_upload_excel_completes_pipeline(client, db):
    """XLSX 上传走 openpyxl 提取器同样到 extracted。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")

    xlsx_bytes = make_excel([{"code": "A", "price": 1}, {"code": "B", "price": 2}])
    r = client.post(
        "/api/documents/upload",
        files={
            "file": (
                "products.xlsx",
                xlsx_bytes,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file_type"] == "excel"
    assert body["status"] == "extracted"


def test_upload_unsupported_extension_returns_400(client, db):
    """白名单外的扩展（.exe）必须 400。否则我们就在帮人传播二进制。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    r = client.post(
        "/api/documents/upload",
        files={"file": ("evil.exe", b"MZ\x90\x00", "application/octet-stream")},
        headers=headers,
    )
    assert r.status_code == 400


def test_upload_extensionless_filename_returns_400(client, db):
    """没有扩展名的文件 → detect_file_type 返回 None → service.BadRequest →
    400（"不支持的文件类型"）。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    r = client.post(
        "/api/documents/upload",
        files={"file": ("README", b"hello", "application/octet-stream")},
        headers=headers,
    )
    assert r.status_code == 400


# ─── GET /api/documents ───────────────────────────────────────


def test_list_documents_returns_paginated_shape(client, db):
    """列表必须返回 PaginatedDocumentsResponse 的 total + items 形状。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    _upload_pdf(client, headers, "a.pdf")
    _upload_pdf(client, headers, "b.pdf")

    r = client.get("/api/documents", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "total" in body
    assert "items" in body
    assert body["total"] >= 2
    assert len(body["items"]) >= 2


def test_list_shows_all_users_documents_company_wide(client, db):
    """2026-07-03: documents are company-wide. B sees A's uploads in the list,
    with uploader_email/uploader_name populated so the UI can show provenance."""
    seed_user(db, email="alice@example.com", role="employee")
    seed_user(db, email="bob@example.com", role="employee")
    a_headers = login(client, "alice@example.com")
    b_headers = login(client, "bob@example.com")

    a_doc = _upload_pdf(client, a_headers, "alice.pdf")

    r = client.get("/api/documents", headers=b_headers)
    assert r.status_code == 200
    items = r.json()["items"]
    matched = [d for d in items if d["id"] == a_doc["id"]]
    assert matched, "B should see A's document in the company-wide list"
    assert matched[0]["uploader_email"] == "alice@example.com"


# ─── GET /api/documents/{id} ──────────────────────────────────


def test_get_document_returns_detail_with_markdown(client, db):
    """详情端点返回 DocumentDetailResponse（包含 content_markdown）。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    doc = _upload_pdf(client, headers)

    r = client.get(f"/api/documents/{doc['id']}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == doc["id"]
    assert "content_markdown" in body
    assert "extracted_data" in body
    # The minimal PDF has the text "hello" embedded.
    assert body["content_markdown"]


def test_get_other_users_document_returns_200_company_wide(client, db):
    """2026-07-03: documents are company-wide. B can read A's document detail,
    including content_markdown, without any ownership check."""
    seed_user(db, email="alice@example.com", role="employee")
    seed_user(db, email="bob@example.com", role="employee")
    a_headers = login(client, "alice@example.com")
    b_headers = login(client, "bob@example.com")
    a_doc = _upload_pdf(client, a_headers)

    r = client.get(f"/api/documents/{a_doc['id']}", headers=b_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == a_doc["id"]
    assert body["uploader_email"] == "alice@example.com"


# ─── PATCH /api/documents/{id} ────────────────────────────────


def test_patch_doc_type_changes_classification(client, db, session_factory):
    """PATCH doc_type 把 doc.doc_type 改为新值。系统 tag 会被刷成
    `doc_type:invoice`。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    doc = _upload_pdf(client, headers)

    r = client.patch(
        f"/api/documents/{doc['id']}",
        json={"doc_type": "invoice"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["doc_type"] == "invoice"

    fresh = session_factory()
    try:
        d = fresh.query(Document).filter(Document.id == doc["id"]).one()
        assert d.doc_type == "invoice"
        assert any(t == "doc_type:invoice" for t in (d.tags or []))
    finally:
        fresh.close()


# ─── POST /api/documents/{id}/user-tags ───────────────────────


def test_add_user_tag_persists(client, db, session_factory):
    """添加用户 tag → service 归一化（kebab-case）+ 写 user_tags JSON 列。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    doc = _upload_pdf(client, headers)

    r = client.post(
        f"/api/documents/{doc['id']}/user-tags",
        json={"tag": "Celebrity Cruise"},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "celebrity-cruise" in (body["user_tags"] or [])

    fresh = session_factory()
    try:
        d = fresh.query(Document).filter(Document.id == doc["id"]).one()
        assert "celebrity-cruise" in (d.user_tags or [])
    finally:
        fresh.close()


def test_remove_user_tag_drops_it(client, db, session_factory):
    """删除已存在的 user_tag 后 list 里不再出现该 tag。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    doc = _upload_pdf(client, headers)

    client.post(
        f"/api/documents/{doc['id']}/user-tags",
        json={"tag": "urgent"},
        headers=headers,
    )
    r = client.delete(
        f"/api/documents/{doc['id']}/user-tags/urgent", headers=headers
    )
    assert r.status_code == 200
    assert "urgent" not in (r.json()["user_tags"] or [])

    fresh = session_factory()
    try:
        d = fresh.query(Document).filter(Document.id == doc["id"]).one()
        assert "urgent" not in (d.user_tags or [])
    finally:
        fresh.close()


# ─── GET /api/documents/{id}/order-payload ────────────────────


def test_order_payload_returns_preview_for_non_po(client, db):
    """非 PO 文档：返回 ready_for_order_creation=False + missing_fields 不空。
    （Phase 3 行为：order_metadata = extracted_data.metadata，可能为空 dict。）"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    doc = _upload_pdf(client, headers)

    r = client.get(f"/api/documents/{doc['id']}/order-payload", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["document_id"] == doc["id"]
    assert body["ready_for_order_creation"] is False
    # No products were extracted from the toy PDF, so blocking missing fields include "no_products".
    assert "no_products" in body["blocking_missing_fields"]


# ─── POST /api/documents/{id}/create-order ────────────────────


def test_create_order_rejects_non_po_without_force(client, db):
    """文档默认 doc_type='unknown'，调 create-order 必须 400（"传 force=true 强制创建"）。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    doc = _upload_pdf(client, headers)

    r = client.post(
        f"/api/documents/{doc['id']}/create-order",
        json={"force": False},
        headers=headers,
    )
    assert r.status_code == 400


# ─── DELETE /api/documents/{id} ───────────────────────────────


def test_delete_removes_document_row(client, db, session_factory):
    """DELETE 后 DB 里不再能查到该 document。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    doc = _upload_pdf(client, headers)

    r = client.delete(f"/api/documents/{doc['id']}", headers=headers)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    fresh = session_factory()
    try:
        d = fresh.query(Document).filter(Document.id == doc["id"]).one_or_none()
        assert d is None
    finally:
        fresh.close()


# ─── GET /api/documents/{id}/file ────────────────────────────


def test_stream_file_returns_pdf_bytes(client, db):
    """流端点返回原始文件字节 + 正确的 Content-Type。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    doc = _upload_pdf(client, headers, filename="x.pdf")

    r = client.get(f"/api/documents/{doc['id']}/file", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content.startswith(b"%PDF-")
