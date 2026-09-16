"""Section 9 — Web API: document rename (P3 2026-06-21).

为什么这套测试存在：
    Prod 用户报告"文档中心无法重命名" —— `filename` 是上传时的原始
    名，对人不友好。P3 引入 `display_name` 列 + PATCH /display-name
    端点让用户自己起标题。本测试锁住三件事：
    (a) 端点契约 (PATCH 改 display_name 返回更新后的 detail)
    (b) None / 空白 / 超长字符串的边界处理
    (c) 跨用户隔离 (Alice 改不到 Bob 的文档)
"""

from __future__ import annotations

from domains.document.models import Document
from test_v2.fixtures.helpers import login, make_minimal_pdf, seed_user


def _upload(client, headers, filename: str = "test.pdf") -> dict:
    pdf = make_minimal_pdf("hi")
    r = client.post(
        "/api/documents/upload",
        files={"file": (filename, pdf, "application/pdf")},
        headers=headers,
    )
    assert r.status_code == 200
    return r.json()


def test_rename_sets_display_name(client, db, session_factory):
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    doc = _upload(client, headers, filename="invoice_20260619.pdf")

    r = client.patch(
        f"/api/documents/{doc['id']}/display-name",
        json={"display_name": "Sydney 2026-06"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "Sydney 2026-06"
    # filename stays put — display_name is *additional*, not a replacement.
    assert r.json()["filename"] == "invoice_20260619.pdf"

    fresh = session_factory()
    try:
        d = fresh.get(Document, doc["id"])
        assert d.display_name == "Sydney 2026-06"
    finally:
        fresh.close()


def test_rename_with_null_clears_display_name(client, db):
    """Passing None reverts UI to showing the original filename."""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    doc = _upload(client, headers)
    # Set then clear.
    client.patch(
        f"/api/documents/{doc['id']}/display-name",
        json={"display_name": "x"},
        headers=headers,
    )
    r = client.patch(
        f"/api/documents/{doc['id']}/display-name",
        json={"display_name": None},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["display_name"] is None


def test_rename_treats_whitespace_as_clear(client, db):
    """Whitespace-only input renders as a blank header in the UI — equivalent
    to "user cleared the field". Service normalizes both to None so the
    column stays clean."""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    doc = _upload(client, headers)
    r = client.patch(
        f"/api/documents/{doc['id']}/display-name",
        json={"display_name": "   "},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["display_name"] is None


def test_rename_truncates_overlong_input(client, db):
    """Hard cap matches the DB column (255). Truncation is preferred over
    a 400 because the frontend already caps the input UX-wise; a hard
    error mid-rename would feel arbitrary."""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    doc = _upload(client, headers)
    long_name = "a" * 500
    r = client.patch(
        f"/api/documents/{doc['id']}/display-name",
        json={"display_name": long_name},
        headers=headers,
    )
    assert r.status_code == 200
    assert len(r.json()["display_name"]) == 255


def test_rename_is_company_wide_across_users(client, db):
    """2026-07-03: rename is a safe integration op (like tag / move / metadata).
    Any logged-in user can rename any document — provenance stays visible via
    uploader_email. Destructive ops (delete, doc_type, reextract) still stay
    owner-gated."""
    seed_user(db, email="alice@example.com", role="employee")
    seed_user(db, email="bob@example.com", role="employee")
    alice_headers = login(client, "alice@example.com")
    bob_headers = login(client, "bob@example.com")
    bobs_doc = _upload(client, bob_headers)
    r = client.patch(
        f"/api/documents/{bobs_doc['id']}/display-name",
        json={"display_name": "renamed-by-alice"},
        headers=alice_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["display_name"] == "renamed-by-alice"
    # Bob's uploader identity is preserved even after Alice renames.
    assert body["uploader_email"] == "bob@example.com"
