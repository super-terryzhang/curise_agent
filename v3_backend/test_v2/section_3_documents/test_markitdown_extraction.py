"""Section 3 — Documents: MarkItDownExtractor — long-tail format adapter.

测试目标：
    `MarkItDownExtractor` 接管 Word/PowerPoint/legacy Excel/HTML/CSV/JSON/
    XML/Markdown/纯文本——也就是 pypdfium2 和 openpyxl 不碰的所有格式。

为什么重要：
    - 必须 NOT claim application/pdf and modern .xlsx（否则跟轻量的 pypdfium2
      / openpyxl 抢，markitdown 的依赖链更重）
    - 必须 claim 整张 long-tail MIME 表，否则 router 会抛 no_extractor
    - 不同子格式的失败要落到合适的 kind（input vs unconfigured vs no_extractor），
      因为 workflow 用 kind 决定 status

设计方法：
    - supports() 是纯 set 查表，参数化即可。
    - extract() 用小份合成 bytes（CSV/JSON/XML/MD/TXT 都是纯文本，直接 .encode()）
    - Word 用 stdlib zipfile 合成一个最小 .docx — markitdown 走 mammoth 路径。
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from domains.document.extraction.base import ExtractionError
from domains.document.extraction.markitdown_extractor import MarkItDownExtractor
from domains.document.extraction.schema import EXTRACTION_SCHEMA_VERSION


# ─── supports() — declared scope ─


@pytest.mark.parametrize(
    "mime",
    [
        # Word
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "word",
        # PowerPoint
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "powerpoint",
        # Legacy Excel binary
        "application/vnd.ms-excel",
        "xls",
        # HTML
        "text/html",
        # Plain-text family
        "text/plain",
        "text",
        "text/csv",
        "csv",
        "text/markdown",
        "markdown",
        "application/json",
        "json",
        "application/xml",
        "text/xml",
        "xml",
    ],
)
def test_supports_long_tail_mimes(mime):
    assert MarkItDownExtractor().supports(mime) is True


@pytest.mark.parametrize(
    "mime",
    [
        "application/pdf",  # pypdfium2 owns this
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # openpyxl
        "image/jpeg",  # vision owns images
        "image/png",
        "application/octet-stream",
    ],
)
def test_does_not_claim_pdf_or_modern_xlsx_or_images(mime):
    """The registration order (pypdfium → openpyxl → markitdown → vision)
    only works if markitdown firmly does NOT claim these mimes."""
    assert MarkItDownExtractor().supports(mime) is False


# ─── extract() — plain-text family ─


def test_extract_csv_renders_table_markdown():
    csv_bytes = b"sku,price\nA-1,10\nB-2,20\n"
    result = MarkItDownExtractor().extract(csv_bytes, "text/csv")
    assert "A-1" in result["markdown"]
    assert "B-2" in result["markdown"]
    assert result["stats"]["extractor"] == "markitdown"
    assert result["schema_version"] == EXTRACTION_SCHEMA_VERSION


def test_extract_json_returns_markdown_with_content():
    json_bytes = b'{"product": "BEEF-01", "qty": 25}'
    result = MarkItDownExtractor().extract(json_bytes, "application/json")
    # markitdown's JSON converter renders the raw structure into the markdown.
    assert "BEEF-01" in result["markdown"]


def test_extract_xml_returns_markdown_with_content():
    xml_bytes = b"<?xml version='1.0'?><root><item>Foo</item></root>"
    result = MarkItDownExtractor().extract(xml_bytes, "application/xml")
    assert "Foo" in result["markdown"]


def test_extract_markdown_preserves_content():
    md_bytes = b"# Heading\n\nSome paragraph text with `code`.\n"
    result = MarkItDownExtractor().extract(md_bytes, "text/markdown")
    assert "Heading" in result["markdown"]
    assert "Some paragraph text" in result["markdown"]


def test_extract_plain_text_passes_through():
    txt = b"line one\nline two\nline three\n"
    result = MarkItDownExtractor().extract(txt, "text/plain")
    assert "line one" in result["markdown"]
    assert "line three" in result["markdown"]


# ─── extract() — Word (DOCX) ─


def _build_minimal_docx(paragraphs: list[str]) -> bytes:
    """Build a minimal Office Open XML .docx with stdlib zipfile only.
    Enough for markitdown's mammoth path to parse."""
    body = "\n".join(
        f'    <w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>'
        for p in paragraphs
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
        "  <w:body>\n"
        f"{body}\n"
        "  </w:body>\n"
        "</w:document>\n"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        '  <Default Extension="xml" ContentType="application/xml"/>\n'
        '  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>\n'
        "</Types>\n"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>\n'
        "</Relationships>\n"
    )
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def test_extract_docx_preserves_paragraph_text():
    docx = _build_minimal_docx(["First paragraph", "Second paragraph"])
    result = MarkItDownExtractor().extract(
        docx,
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document",
    )
    assert "First paragraph" in result["markdown"]
    assert "Second paragraph" in result["markdown"]
    assert result["stats"]["extractor"] == "markitdown"


# ─── extract() — error paths ─


def test_extract_raises_empty_for_zero_bytes():
    with pytest.raises(ExtractionError) as exc:
        MarkItDownExtractor().extract(b"", "text/plain")
    assert exc.value.kind == "empty"


def test_extract_raises_no_extractor_when_caller_bypasses_router():
    """Defensive: someone calls extract() with a MIME we don't have an
    extension hint for — we must NOT pass None to markitdown."""
    with pytest.raises(ExtractionError) as exc:
        MarkItDownExtractor().extract(b"x", "application/x-nope")
    assert exc.value.kind == "no_extractor"
