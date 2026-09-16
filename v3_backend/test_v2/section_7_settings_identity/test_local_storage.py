"""Section 7 — Infrastructure: LocalFileStorage (dev/test backend).

测试目标：
    `infrastructure/storage/local.py` 的契约面:
      - upload(content) → key; download(key) 取回原 bytes (round-trip)
      - 文件名里的 `../` `/` 等不安全字符要 sanitize, 防止 path traversal
      - download 不存在的 key → 抛 StorageError (不静默)
      - get_signed_url 给一个 *稳定* 字符串 (本地实现用 /uploads/ 路径)
      - delete 是 best-effort, 不存在的 key 不报错

为什么重要：
    LocalFileStorage 是 dev/test 环境的所有上传落地点; 一旦它逃逸 root 目录
    或在 download 缺失时返回空 bytes (不抛错), 整个上传/下载链都会安静地
    出错 — 单测必须把这些契约钉死。

设计方法：
    每个测试拿一个 tmp_path → 直接实例化 LocalFileStorage(tmp_path)。
    不用 conftest.py 的 _local_storage 自动 fixture, 因为我们要测 storage
    本身, 不是 storage_module 的 selector。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from infrastructure.storage.base import StorageError
from infrastructure.storage.local import LocalFileStorage
from infrastructure.storage.urls import valid_download_signature


# ─── Round-trip ───────────────────────────────────────────────


def test_upload_download_roundtrip_preserves_bytes(tmp_path: Path) -> None:
    """upload → download 必须 *byte-for-byte* 还原原内容。"""
    storage = LocalFileStorage(tmp_path)
    payload = b"\x00\x01\x02\xff\xfe\xfd binary content with high bytes"
    key = storage.upload("docs", "hello.txt", payload, "text/plain")
    assert key.startswith("docs/")
    assert storage.download(key) == payload


def test_upload_returns_unique_key_for_same_filename(tmp_path: Path) -> None:
    """同名 upload 两次 → 两个不同的 storage_key (前缀加 uuid)。
    否则后传的会覆盖前传的, 而调用方拿到的 key 看起来一样。"""
    storage = LocalFileStorage(tmp_path)
    k1 = storage.upload("docs", "x.pdf", b"v1")
    k2 = storage.upload("docs", "x.pdf", b"v2")
    assert k1 != k2
    assert storage.download(k1) == b"v1"
    assert storage.download(k2) == b"v2"


# ─── Path traversal protection ────────────────────────────────


def test_upload_filename_with_dotdot_is_sanitized(tmp_path: Path) -> None:
    """文件名里 `../` 必须被 sanitize — 落地路径不允许逃出 root.
    实现把 `/` 替换为 `_`, 所以两段 `../` 都不再是分隔符, 而是文件名片段。"""
    storage = LocalFileStorage(tmp_path)
    key = storage.upload("docs", "../../etc/passwd", b"PWN")
    # key 必须仍在 docs/ 下面
    assert key.startswith("docs/")
    # 文件名段里不允许出现路径分隔符 (确保不会逃出 docs/ 子目录)
    parts_after_prefix = key[len("docs/"):]
    assert "/" not in parts_after_prefix, f"sanitize 失败: 路径分隔符泄漏 {key}"
    # 文件确实落在 tmp_path 之内 (resolve 后还是 tmp_path 的子节点)
    abs_path = (tmp_path / key).resolve()
    assert str(abs_path).startswith(str(tmp_path.resolve()))


def test_upload_filename_with_slash_is_sanitized(tmp_path: Path) -> None:
    """`/etc/passwd` 形式 — / 也得替换成 _ 之类。"""
    storage = LocalFileStorage(tmp_path)
    key = storage.upload("docs", "etc/passwd", b"x")
    # key 形如 docs/<uuid>_etc_passwd —— 不能出现 etc/passwd 子目录
    parts_after_prefix = key[len("docs/"):]
    assert "/" not in parts_after_prefix, f"sanitize 失败: {key}"


def test_upload_special_chars_in_filename_are_replaced(tmp_path: Path) -> None:
    """空格/中文/$ ! 等 — 替换成下划线, 但扩展名保留。"""
    storage = LocalFileStorage(tmp_path)
    key = storage.upload("docs", "hello world!.pdf", b"x")
    # 扩展名保留
    assert key.endswith(".pdf")
    # 文件名里没空格 / !
    assert " " not in key
    assert "!" not in key


# ─── Signed URL ───────────────────────────────────────────────


def test_get_signed_url_returns_uploads_relative_path(tmp_path: Path) -> None:
    """Local fallback also uses an expiring resource-scoped signature."""
    storage = LocalFileStorage(tmp_path)
    key = storage.upload("docs", "x.txt", b"hello")
    url = storage.get_signed_url(key)
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    expires = int(query["expires"][0])
    signature = query["signature"][0]
    assert parsed.path == f"/uploads/{key}"
    assert valid_download_signature(key, expires, signature)


def test_get_signed_url_respects_requested_expiry(tmp_path: Path) -> None:
    """Different expiry windows must produce different signed credentials."""
    storage = LocalFileStorage(tmp_path)
    key = storage.upload("docs", "x.txt", b"hello")
    assert storage.get_signed_url(key) != storage.get_signed_url(key, expires_in=999)


# ─── Error paths ──────────────────────────────────────────────


def test_download_missing_key_raises_storage_error(tmp_path: Path) -> None:
    """不存在的 key → StorageError, 不能返回 b'' (那样 caller 当成空文件)。"""
    storage = LocalFileStorage(tmp_path)
    with pytest.raises(StorageError):
        storage.download("docs/does-not-exist.pdf")


def test_delete_missing_key_is_silent(tmp_path: Path) -> None:
    """delete 是 best-effort — 不存在的 key 不报错, 这样幂等。"""
    storage = LocalFileStorage(tmp_path)
    storage.delete("docs/does-not-exist.pdf")  # 不抛错


def test_delete_existing_key_removes_file(tmp_path: Path) -> None:
    """delete 真的把磁盘上的文件清掉, 之后 download 拿不到了。"""
    storage = LocalFileStorage(tmp_path)
    key = storage.upload("docs", "tmp.txt", b"data")
    assert storage.download(key) == b"data"
    storage.delete(key)
    with pytest.raises(StorageError):
        storage.download(key)


# ─── Constructor invariants ────────────────────────────────────


def test_constructor_creates_root_directory_if_missing(tmp_path: Path) -> None:
    """root 目录不存在 → 自动 mkdir, 否则首次 upload 就崩。"""
    root = tmp_path / "fresh_storage_root"
    assert not root.exists()
    LocalFileStorage(root)
    assert root.exists() and root.is_dir()
