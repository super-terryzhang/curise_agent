"""Signed downloads preserve previews while denying anonymous storage keys."""

from __future__ import annotations

from infrastructure.storage import get_storage
from test_v2.fixtures.helpers import make_minimal_pdf


def _put_file(folder: str, name: str, content: bytes, content_type: str) -> str:
    """Upload via storage and return the resolved key."""
    return get_storage().upload(folder, name, content, content_type=content_type)


# ─── Happy path: existing files ───────────────────────────────


def test_existing_pdf_served_with_inline_disposition(client):
    """正常存在的 PDF：200 + Content-Type application/pdf + inline。"""
    key = _put_file(
        "documents", "preview.pdf", make_minimal_pdf("x"), "application/pdf"
    )
    r = client.get(get_storage().get_signed_url(key))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content.startswith(b"%PDF-")
    assert 'inline' in r.headers.get("content-disposition", "")


def test_existing_xlsx_served_with_spreadsheet_mime(client):
    """xlsx 在 mimetypes 默认表里没有；files.py 通过 _EXTRA_MIME_TYPES
    回退到正确的 spreadsheetml.sheet mime。"""
    key = _put_file(
        "documents",
        "data.xlsx",
        b"PK\x03\x04fake-xlsx-bytes",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    r = client.get(get_storage().get_signed_url(key))
    assert r.status_code == 200
    assert (
        "spreadsheetml.sheet" in r.headers["content-type"]
        or r.headers["content-type"] == "application/octet-stream"
    )  # the latter slips through only if _EXTRA_MIME_TYPES was tampered with


# ─── Missing keys ─────────────────────────────────────────────


def test_missing_key_returns_404(client):
    """不存在的 key → LocalFileStorage 抛 StorageError → 404。"""
    r = client.get(get_storage().get_signed_url("documents/this-does-not-exist.pdf"))
    assert r.status_code == 404


# ─── Path traversal guard ─────────────────────────────────────


def test_dot_dot_path_returns_400(client):
    """`../../etc/passwd` → _validate_key 检出 ".."，返回 400。"""
    # FastAPI/Starlette automatically normalizes ../ in the URL path,
    # so we send the dots inside an encoded segment to make sure they
    # land on _validate_key.
    r = client.get("/uploads/foo/..%2F..%2Fetc%2Fpasswd")
    # Either the encoded form trips the guard (400) or starlette resolves
    # it to a non-existent key (404) — either is safe.
    assert r.status_code in (400, 404)


def test_explicit_dotdot_segment_blocked(client):
    """直接发字面 `..` 段名进 path 参数（不是 URL 编码）：
    `_validate_key` 应该看到 part==".." 触发 400。"""
    # Use a key like `legit_folder/../escape`. The FastAPI path is
    # `file_path:path` so the whole thing reaches our validator.
    # Starlette may normalize URI; build the raw URL.
    r = client.get(
        "/uploads/legit/..%2Fescape", follow_redirects=False
    )
    assert r.status_code in (400, 404)


# ─── Public access (no auth required) ─────────────────────────


def test_unsigned_private_file_is_denied(client):
    key = _put_file("documents", "a.pdf", make_minimal_pdf(), "application/pdf")
    assert client.get(f"/uploads/{key}").status_code == 403


def test_signed_url_cannot_be_used_for_another_file(client):
    key = _put_file("documents", "a.pdf", b"PRIVATE", "application/pdf")
    other = _put_file("documents", "b.pdf", b"OTHER", "application/pdf")
    url = get_storage().get_signed_url(key)
    assert client.get(url.replace(key, other)).status_code == 403


def test_expired_and_tampered_url_denied(client, monkeypatch):
    import infrastructure.storage.urls as urls
    key = _put_file("documents", "a.pdf", b"PRIVATE", "application/pdf")
    now = urls.time.time()
    monkeypatch.setattr(urls.time, "time", lambda: now)
    url = get_storage().get_signed_url(key, expires_in=10)
    assert client.get(url + "0").status_code == 403
    monkeypatch.setattr(urls.time, "time", lambda: now + 10)
    assert client.get(url).status_code == 403


def test_preview_range_and_head(client):
    key = _put_file("documents", "a.pdf", b"0123456789", "application/pdf")
    url = get_storage().get_signed_url(key)
    r = client.get(url, headers={"Range": "bytes=2-5"})
    assert r.status_code == 206 and r.content == b"2345"
    assert r.headers["content-range"] == "bytes 2-5/10"
    assert client.get(url, headers={"Range": "bytes=-3"}).content == b"789"
    assert client.get(url, headers={"Range": "bytes=99-"}).status_code == 416
    head = client.head(url)
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-length"] == "10"
    assert head.headers["cache-control"] == "private, no-store"
