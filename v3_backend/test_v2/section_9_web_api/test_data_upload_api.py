"""Section 9 — Web API: `/api/data-upload/upload`.

测试目标：
    单一 endpoint 但是 master-data 系统的入口——Excel 上传后返回 batch_id，
    后续 agent 工具用 batch_id 去 preview/commit。这里验证：
    - 合法 xlsx → 200 + batch_id + StagingProduct 写入 DB
    - 无效格式（非 xlsx 字节）→ 400
    - 缺必需列 → 400（解析器走 ParseError 路径）
    - 没有 auth header → 401（FastAPI HTTPBearer auto_error）
    - 超大文件 → 413（settings.MAX_UPLOAD_SIZE）
    - 用户 A 不能看到用户 B 的 batch（这是 service 层的 user_id scoping
      已经验证过的，但这里通过 list_batches 的不存在性侧面验证）

为什么重要：
    上传是 chat agent 与文件的唯一交汇点；如果 batch_id 不正确写回 DB，
    后续 preview_upload 找不到任何东西。Size 限制如果挂了内存就会被吃光。

设计方法：
    用 `make_excel` 生成 xlsx 字节。MAX_UPLOAD_SIZE 测试用 monkeypatch
    把 settings 上的限制临时调小到 100 字节，避免真的造 30MB 文件。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from domains.masterdata.upload.models import StagingProduct, UploadBatch
from test_v2.fixtures.helpers import login, make_excel, seed_user


def _employee_headers(client: TestClient, session_factory) -> dict[str, str]:
    """Seed an employee + return Authorization headers."""
    db = session_factory()
    try:
        seed_user(db, email="uploader@example.com", role="employee")
    finally:
        db.close()
    return login(client, "uploader@example.com")


# ─── Happy path ─────────────────────────────────────────────


def test_upload_valid_excel_creates_batch(client, session_factory):
    """Valid xlsx → 200, batch_id > 0, total_rows matches, and
    StagingProduct rows actually exist."""
    headers = _employee_headers(client, session_factory)
    blob = make_excel(
        [
            {"product_code": "A1", "product_name": "Apple", "price": 10.0},
            {"product_code": "B2", "product_name": "Banana", "price": 5.0},
            {"product_code": "C3", "product_name": "Cherry", "price": 2.5},
        ]
    )
    r = client.post(
        "/api/data-upload/upload",
        files={
            "file": (
                "products.xlsx",
                blob,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["batch_id"] > 0
    assert body["filename"] == "products.xlsx"
    assert body["status"] == "ready"
    assert body["total_rows"] == 3

    # DB check — staging rows landed.
    db = session_factory()
    try:
        batch = db.get(UploadBatch, body["batch_id"])
        assert batch is not None
        n = (
            db.query(StagingProduct)
            .filter(StagingProduct.batch_id == batch.id)
            .count()
        )
        assert n == 3
    finally:
        db.close()


# ─── Invalid format ─────────────────────────────────────────


def test_upload_non_excel_bytes_returns_400(client, session_factory):
    """Garbage bytes are not a workbook → ParseError → 400."""
    headers = _employee_headers(client, session_factory)
    r = client.post(
        "/api/data-upload/upload",
        files={"file": ("not_excel.xlsx", b"hello world this is not xlsx", "application/octet-stream")},
        headers=headers,
    )
    assert r.status_code == 400, r.text
    # The message includes "解析失败" prefix from the route.
    assert "解析" in r.json()["detail"] or "could not open" in r.json()["detail"]


def test_upload_missing_required_column_returns_400(client, session_factory):
    """A workbook with headers that don't include product_name (or its aliases)
    → ParseError → 400."""
    headers = _employee_headers(client, session_factory)
    blob = make_excel(
        [
            {"random_column": "A1", "another_random": "x"},
            {"random_column": "B2", "another_random": "y"},
        ]
    )
    r = client.post(
        "/api/data-upload/upload",
        files={"file": ("bad.xlsx", blob, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert r.status_code == 400, r.text


def test_upload_empty_file_returns_400(client, session_factory):
    """Zero-byte file → "文件为空" 400."""
    headers = _employee_headers(client, session_factory)
    r = client.post(
        "/api/data-upload/upload",
        files={"file": ("empty.xlsx", b"", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert r.status_code == 400, r.text


# ─── Auth ───────────────────────────────────────────────────


def test_upload_without_auth_returns_401(client):
    """No bearer token → 401 (FastAPI HTTPBearer auto_error)."""
    blob = make_excel([{"product_name": "X", "price": 1.0}])
    r = client.post(
        "/api/data-upload/upload",
        files={"file": ("x.xlsx", blob, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code in (401, 403), r.text


# ─── Size limit ─────────────────────────────────────────────


def test_upload_too_large_returns_413(client, session_factory, monkeypatch):
    """File exceeding MAX_UPLOAD_SIZE → 413.

    We shrink the limit to 100 bytes via monkeypatch so we don't have to
    build a 30MB blob.
    """
    monkeypatch.setattr(
        "apps.http.data_upload.settings.MAX_UPLOAD_SIZE",
        100,
    )
    headers = _employee_headers(client, session_factory)
    # Real xlsx will be >100B easily.
    blob = make_excel([{"product_name": "X", "price": 1.0}])
    assert len(blob) > 100  # sanity

    r = client.post(
        "/api/data-upload/upload",
        files={"file": ("big.xlsx", blob, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert r.status_code == 413, r.text


# ─── User scoping ──────────────────────────────────────────


def test_users_only_see_their_own_batches(client, session_factory):
    """User A's upload is scoped to user A. User B making a separate
    upload doesn't see user A's batch via the service-level list.

    The HTTP layer doesn't expose a 'list batches' endpoint (that's an
    agent tool), but we can verify the DB scoping invariant: each batch
    row carries user_id pointing to the uploader.
    """
    # User A
    db_a = session_factory()
    try:
        a = seed_user(db_a, email="alice-upload@example.com", role="employee")
        a_id = a.id
    finally:
        db_a.close()
    auth_a = login(client, "alice-upload@example.com")

    # User B
    db_b = session_factory()
    try:
        b = seed_user(db_b, email="bob-upload@example.com", role="employee")
        b_id = b.id
    finally:
        db_b.close()
    auth_b = login(client, "bob-upload@example.com")

    blob = make_excel([{"product_name": "Foo", "price": 1.0}])

    r_a = client.post(
        "/api/data-upload/upload",
        files={"file": ("a.xlsx", blob, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth_a,
    )
    assert r_a.status_code == 200
    batch_a_id = r_a.json()["batch_id"]

    r_b = client.post(
        "/api/data-upload/upload",
        files={"file": ("b.xlsx", blob, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth_b,
    )
    assert r_b.status_code == 200
    batch_b_id = r_b.json()["batch_id"]

    # Each batch row's user_id matches its uploader, never crosses.
    db = session_factory()
    try:
        ba = db.get(UploadBatch, batch_a_id)
        bb = db.get(UploadBatch, batch_b_id)
        assert ba.user_id == a_id
        assert bb.user_id == b_id
        assert ba.user_id != bb.user_id
    finally:
        db.close()
