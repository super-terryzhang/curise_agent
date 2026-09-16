"""Section 6 — Masterdata upload pipeline, HTTP endpoint layer.

测试目标：
    `POST /api/data-upload/upload` 是整条流水线的入口。Service unit 测试只
    跑 `parse_excel(...)`，覆盖不到 FastAPI 装填/解构 multipart、auth
    middleware、大小限制、空文件保护这些边界。这个文件就是测这些。

为什么重要：
    一个匿名用户能上传 100MB 垃圾文件、或者一个空文件让后端抛 500 都是
    生产可见的稳定性 / 安全问题。前端的 `data-upload` 仅是包装 — 真正决
    定行为的是这个 endpoint 自己。

设计方法：
    用 TestClient 真发 multipart 请求；fixture `client` 提供 DB 隔离 +
    auth；用 `make_excel()` 生成真实 xlsx 字节，避免引入 sample file。
"""

from __future__ import annotations

import io

from openpyxl import Workbook

from test_v2.fixtures.helpers import login, make_excel, seed_user


# ─── Helpers ─────────────────────────────────────────────────


def _post_upload(client, headers, *, filename: str, blob: bytes):
    """POST multipart with one file field, mirroring the frontend's
    `uploadDataFile(file)` FormData call."""
    return client.post(
        "/api/data-upload/upload",
        headers=headers,
        files={"file": (filename, blob, "application/octet-stream")},
    )


# ─── Authentication ──────────────────────────────────────────


def test_upload_requires_auth_token(client, db):
    """No Authorization header → FastAPI's HTTPBearer rejects with 401/403.
    Anonymous upload would let anyone fill our staging tables."""
    blob = make_excel([{"product_name": "X"}])
    # No headers provided.
    r = client.post(
        "/api/data-upload/upload",
        files={"file": ("x.xlsx", blob, "application/octet-stream")},
    )
    assert r.status_code in (401, 403)


def test_upload_accepts_finance_role(client, db):
    """Finance has writer permissions including masterdata upload.

    Policy update (2026-06-08 Felix decision, see
    docs/current_progress/2026-06-16/R3_finance_role.md): finance users
    (JP staff owning the FinancialTab) get the full employee write
    surface. The previous inverse assertion blocked finance staff from
    day-to-day order/masterdata work, which contradicted the new
    operating model where finance is a writer + financial steward.
    Asserts 200 to lock in the current `require_writer` membership."""
    seed_user(db, email="finance@example.com", role="finance")
    headers = login(client, "finance@example.com")
    blob = make_excel(
        [
            {
                "product_code": "FIN1",
                "product_name": "Finance test product",
                "price": 1.0,
            }
        ]
    )
    r = _post_upload(client, headers, filename="x.xlsx", blob=blob)
    assert r.status_code == 200, r.text


# ─── Happy path ──────────────────────────────────────────────


def test_upload_returns_batch_id_and_total_rows_on_success(client, db):
    """3-row valid file → 200 + `{batch_id, filename, status, total_rows}`."""
    seed_user(db, email="emp@example.com")
    headers = login(client, "emp@example.com")
    blob = make_excel(
        [
            {"product_code": "A1", "product_name": "Apple", "price": 10.0},
            {"product_code": "B1", "product_name": "Banana", "price": 5.0},
            {"product_code": "C1", "product_name": "Cherry", "price": 25.0},
        ]
    )
    r = _post_upload(client, headers, filename="produce.xlsx", blob=blob)

    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["batch_id"], int)
    assert body["filename"] == "produce.xlsx"
    assert body["status"] == "ready"
    assert body["total_rows"] == 3


# ─── Input validation ────────────────────────────────────────


def test_upload_rejects_empty_file_with_400(client, db):
    """0-byte uploads → 400 with "文件为空", not a 500 / a corrupt-parse
    error. Frontend can show a clear message."""
    seed_user(db, email="emp@example.com")
    headers = login(client, "emp@example.com")
    r = _post_upload(client, headers, filename="empty.xlsx", blob=b"")
    assert r.status_code == 400
    assert "为空" in r.text


def test_upload_rejects_oversize_file_with_413(client, db, monkeypatch):
    """Files larger than MAX_UPLOAD_SIZE → 413 (Payload Too Large).
    We patch the setting to a tiny value so we don't materialise a real
    30MB blob in test memory."""
    from infrastructure.config import settings as cfg
    from apps.http import data_upload as endpoint_module

    monkeypatch.setattr(endpoint_module.settings, "MAX_UPLOAD_SIZE", 32)

    seed_user(db, email="emp@example.com")
    headers = login(client, "emp@example.com")
    # Any valid xlsx is >32 bytes, so this trips the size guard.
    blob = make_excel([{"product_name": "Apple"}])
    assert len(blob) > 32  # sanity: we will actually exceed
    r = _post_upload(client, headers, filename="big.xlsx", blob=blob)
    assert r.status_code == 413
    # Verify the guard didn't actually parse the file.
    from domains.masterdata.upload.models import UploadBatch

    assert db.query(UploadBatch).count() == 0


def test_upload_rejects_corrupt_xlsx_with_400(client, db):
    """Non-xlsx bytes → 400, body contains the parse failure reason
    instead of a 500. Frontend shows it in a toast."""
    seed_user(db, email="emp@example.com")
    headers = login(client, "emp@example.com")
    r = _post_upload(client, headers, filename="corrupt.xlsx", blob=b"this is not xlsx")
    assert r.status_code == 400
    assert "解析失败" in r.text or "could not open" in r.text


def test_upload_rejects_xlsx_without_product_name_column_with_400(client, db):
    """File parses as a workbook but has no recognised name column → 400
    "no `product_name` column". This is the most common user mistake
    (e.g. they shipped supplier code only)."""
    wb = Workbook()
    ws = wb.active
    ws.append(["random_col", "another_col"])
    ws.append(["X", 7])
    buf = io.BytesIO()
    wb.save(buf)

    seed_user(db, email="emp@example.com")
    headers = login(client, "emp@example.com")
    r = _post_upload(client, headers, filename="no-name.xlsx", blob=buf.getvalue())
    assert r.status_code == 400
    assert "product_name" in r.text or "name" in r.text
