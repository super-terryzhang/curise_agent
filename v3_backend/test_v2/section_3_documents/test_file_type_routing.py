"""Section 3 — Documents: file_types whitelist + detection.

测试目标：
    `domains.document.file_types` 是文件白名单的唯一真相源。
    任何 upload/download/工作流的 MIME 路由都来自这张表。

为什么重要：
    曾有 4 个地方各维护一份 MIME 映射，2026-05-01 才合并到 file_types.py。
    一旦这张表漂移：上传成功的文件用错 Content-Type 下载、抽取器拿不到
    它声明 supports() 的 MIME、UI 报"不支持"但其实支持……所以白名单的
    完整性 + 大小写 + 黑名单都要锁死。

设计方法：
    - 每个允许的扩展名都参数化跑 detect_file_type → 断言短 tag。
    - 视频/可执行/压缩包/未知扩展名一律返回 None（上层应拒绝）。
    - 大小写 / 路径 / 多点 / 无扩展 = 边界条件。
    - file_type → mime 反向查询断言所有 tag 都能 round-trip。
"""

from __future__ import annotations

import pytest

from domains.document.file_types import (
    EXTENSION_TO_FILE_TYPE,
    FILE_TYPE_TO_MIME,
    default_content_type,
    detect_file_type,
    supported_extensions,
)


# ─── detect_file_type: every allowed extension routes correctly ─


@pytest.mark.parametrize(
    "filename,expected_tag",
    [
        # Documents
        ("report.pdf", "pdf"),
        ("contract.docx", "word"),
        ("legacy.doc", "word"),
        ("manifest.xlsx", "excel"),
        ("old_sheet.xls", "xls"),
        ("data.csv", "csv"),
        ("readme.txt", "text"),
        ("notes.md", "markdown"),
        ("notes.markdown", "markdown"),
        ("config.json", "json"),
        ("feed.xml", "xml"),
        ("deck.pptx", "powerpoint"),
        ("deck.ppt", "powerpoint"),
        # Images — file_type IS the mime for image/* (image tags ARE mimes)
        ("photo.jpg", "image/jpeg"),
        ("photo.jpeg", "image/jpeg"),
        ("logo.png", "image/png"),
        ("anim.gif", "image/gif"),
        ("hero.webp", "image/webp"),
        ("iphone.heic", "image/heic"),
        ("iphone.heif", "image/heic"),
        ("scan.bmp", "image/bmp"),
        ("scan.tif", "image/tiff"),
        ("scan.tiff", "image/tiff"),
    ],
)
def test_detect_file_type_for_every_allowed_extension(filename, expected_tag):
    """Every documented extension in EXTENSION_TO_FILE_TYPE table maps to
    the expected short tag. This is the load-bearing whitelist."""
    assert detect_file_type(filename) == expected_tag


# ─── detect_file_type: blacklist — never accept these ─


@pytest.mark.parametrize(
    "filename",
    [
        # video — out of scope by design
        "movie.mp4",
        "clip.mov",
        "show.mkv",
        "rec.avi",
        # audio
        "song.mp3",
        "voice.wav",
        # executables — security risk
        "virus.exe",
        "script.bat",
        "shell.sh",
        "lib.dll",
        "tool.app",
        # archives — out of scope
        "bundle.zip",
        "src.tar",
        "src.gz",
        "src.rar",
        "src.7z",
        # databases — out of scope
        "data.sqlite",
        "data.db",
        # unknown
        "mystery.xyz",
        "weird.qqq",
    ],
)
def test_detect_file_type_rejects_blacklisted_and_unknown(filename):
    """Anything outside the whitelist returns None so upload_document
    can raise BadRequest with the supported_extensions() list."""
    assert detect_file_type(filename) is None


# ─── detect_file_type: edge cases ─


def test_detect_file_type_is_case_insensitive():
    """Uppercase / mixed-case extensions still resolve. Users on Windows
    drag-drop files like `INVOICE.PDF` all the time."""
    assert detect_file_type("INVOICE.PDF") == "pdf"
    assert detect_file_type("Contract.DocX") == "word"
    assert detect_file_type("LOGO.JPG") == "image/jpeg"


def test_detect_file_type_returns_none_for_empty_filename():
    assert detect_file_type("") is None


def test_detect_file_type_returns_none_when_no_extension():
    """`README` with no dot should NOT silently become text/plain."""
    assert detect_file_type("README") is None
    assert detect_file_type("Makefile") is None


def test_detect_file_type_uses_last_dot_for_extension():
    """Multiple dots in filename — only the trailing extension counts."""
    assert detect_file_type("report.v2.final.pdf") == "pdf"
    assert detect_file_type("archive.tar.gz") is None  # .gz is blacklisted


def test_detect_file_type_handles_path_with_directories():
    """Filename param may carry path separators; the basename's extension
    is what determines the tag."""
    assert detect_file_type("/uploads/2026/report.pdf") == "pdf"


# ─── default_content_type: tag → IETF mime round-trip ─


@pytest.mark.parametrize(
    "tag,expected_mime",
    [
        ("pdf", "application/pdf"),
        (
            "word",
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document",
        ),
        (
            "excel",
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet",
        ),
        ("xls", "application/vnd.ms-excel"),
        ("csv", "text/csv"),
        ("text", "text/plain"),
        ("markdown", "text/markdown"),
        ("json", "application/json"),
        ("xml", "application/xml"),
        (
            "powerpoint",
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation",
        ),
        ("image/jpeg", "image/jpeg"),
        ("image/png", "image/png"),
        ("image/heic", "image/heic"),
    ],
)
def test_default_content_type_returns_ietf_mime_for_each_tag(tag, expected_mime):
    assert default_content_type(tag) == expected_mime


def test_default_content_type_falls_back_to_octet_stream_for_unknown_tag():
    """Old DB rows with retired file_types still need *some* Content-Type
    so the file at least downloads. Fallback is generic binary."""
    assert default_content_type("never-existed") == "application/octet-stream"
    assert default_content_type("") == "application/octet-stream"


def test_default_content_type_keeps_legacy_xlsx_alias():
    """Pre-2026-05-01 rows have file_type='xlsx'. They must still serve the
    correct content type so existing files don't break in the UI."""
    assert (
        default_content_type("xlsx")
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# ─── End-to-end round-trip: every tag in the table must have a mime ─


def test_every_extension_tag_has_a_mime_mapping():
    """Each tag produced by EXTENSION_TO_FILE_TYPE must also appear in
    FILE_TYPE_TO_MIME — otherwise upload would silently lose Content-Type."""
    extension_tags = set(EXTENSION_TO_FILE_TYPE.values())
    mime_tags = set(FILE_TYPE_TO_MIME.keys())
    missing = extension_tags - mime_tags
    assert missing == set(), f"tags without mime: {sorted(missing)}"


def test_every_extension_tag_fits_in_20_char_db_column():
    """Document.file_type is String(20). Drifting past 20 chars would
    silently truncate on Postgres in production."""
    for tag in EXTENSION_TO_FILE_TYPE.values():
        assert len(tag) <= 20, f"tag too long: {tag!r}"


# ─── supported_extensions: shape + content ─


def test_supported_extensions_is_sorted_for_ui_display():
    out = supported_extensions()
    assert out == sorted(out)
    assert ".pdf" in out
    assert ".xlsx" in out
    assert ".jpg" in out


def test_supported_extensions_does_not_include_blacklisted():
    out = supported_extensions()
    assert ".exe" not in out
    assert ".zip" not in out
    assert ".mp4" not in out
