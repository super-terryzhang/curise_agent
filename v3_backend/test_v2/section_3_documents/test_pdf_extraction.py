"""Section 3 — Documents: PyPDFiumExtractor — the cheap text-layer path.

测试目标：
    `PyPDFiumExtractor` 直接把 PDF 文本层提取成 markdown，无 LLM 成本。
    born-digital PDF 走这条路；扫描 PDF 走完会返回 markdown="" 让上层
    `PdfOrchestrator` 决定是否 fallback 到 Gemini。

为什么重要：
    系统每天上传的 95% 是 born-digital PDF。这条路径必须：
    - 把文本无损取出（otherwise downstream classifier 看不到 PO 号）
    - 真正坏的 PDF 抛 input/empty 错（不能装作成功，避免 status=extracted
      但 markdown 为空，那是扫描 PDF 的语义）
    - 只 claim application/pdf（otherwise 抢 markitdown 的格式）

设计方法：
    用 `make_minimal_pdf` 合成最小可用 PDF；空字节、损坏 PDF
    自合成。断言 ExtractionError.kind 而不只是 raise — kind 是下游
    workflow 决定 status (stored vs error) 的开关。
"""

from __future__ import annotations

import pytest

from domains.document.extraction.base import ExtractionError
from domains.document.extraction.pypdf_extractor import PyPDFiumExtractor
from domains.document.extraction.schema import EXTRACTION_SCHEMA_VERSION
from test_v2.fixtures.helpers import make_minimal_pdf


# ─── supports() ─


@pytest.mark.parametrize("mime", ["application/pdf", "pdf"])
def test_supports_canonical_pdf_mimes(mime):
    assert PyPDFiumExtractor().supports(mime) is True


def test_supports_any_application_pdf_subtype():
    """application/pdf;charset=binary etc — anything starting with
    application/pdf should match (per the prefix check in supports())."""
    assert PyPDFiumExtractor().supports("application/pdf; charset=binary") is True


@pytest.mark.parametrize(
    "mime",
    [
        "image/jpeg",
        "text/plain",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/json",
    ],
)
def test_does_not_claim_non_pdf_mimes(mime):
    """PDF extractor must not steal mimes owned by other extractors —
    registration order matters and supports() is the firewall."""
    assert PyPDFiumExtractor().supports(mime) is False


# ─── extract() — happy path ─


def test_extract_returns_text_from_valid_pdf():
    """Born-digital PDF → markdown contains the embedded text."""
    pdf = make_minimal_pdf("Hello PDF Phase 3 test")
    result = PyPDFiumExtractor().extract(pdf, "application/pdf")
    assert "Hello PDF Phase 3 test" in result["markdown"]


def test_extract_sets_schema_version_and_extractor_stats():
    """ExtractedDocument must carry schema_version + extractor identity
    so downstream can route by version / debug attribution."""
    pdf = make_minimal_pdf("anything")
    result = PyPDFiumExtractor().extract(pdf, "application/pdf")
    assert result["schema_version"] == EXTRACTION_SCHEMA_VERSION
    assert result["stats"]["extractor"] == "pypdfium2"
    assert result["stats"]["truncated"] is False


def test_extract_reports_page_count():
    """page_count helps the UI render `(3 pages)` next to the doc title.
    The synthesized PDF has exactly one page."""
    pdf = make_minimal_pdf("single page")
    result = PyPDFiumExtractor().extract(pdf, "application/pdf")
    assert result["page_count"] == 1


def test_extract_title_picked_from_first_short_paragraph():
    """A short first line (<120 chars) is heuristically the title — the
    UI uses this for the document detail header."""
    pdf = make_minimal_pdf("Report 2026")
    result = PyPDFiumExtractor().extract(pdf, "application/pdf")
    assert result["title"] == "Report 2026"


# ─── extract() — error paths ─


def test_extract_raises_empty_for_zero_bytes():
    """Empty bytes is not a valid PDF; we don't want to mask it as
    "scanned with no text" — that would silently invoke Gemini."""
    with pytest.raises(ExtractionError) as exc:
        PyPDFiumExtractor().extract(b"", "application/pdf")
    assert exc.value.kind == "empty"


def test_extract_raises_input_for_corrupt_bytes():
    """Random garbage bytes labelled as PDF → kind='input' so workflow
    marks the document `error` (not `stored`)."""
    with pytest.raises(ExtractionError) as exc:
        PyPDFiumExtractor().extract(b"not a real pdf at all", "application/pdf")
    assert exc.value.kind == "input"


def test_extract_returns_empty_markdown_when_text_layer_missing():
    """A PDF with no embedded text (e.g. a pure scan) parses successfully
    but markdown is empty. This is the trigger for PdfOrchestrator's
    Gemini fallback — must NOT raise."""
    # Build a PDF with no /Contents stream by removing the text op
    # cheapest path: use make_minimal_pdf with empty body
    pdf = make_minimal_pdf("")  # PDF with empty/whitespace text stream
    result = PyPDFiumExtractor().extract(pdf, "application/pdf")
    # Either no paragraphs, or paragraphs with only whitespace — both
    # yield an empty markdown stripped value, which is what the
    # orchestrator checks.
    assert result["markdown"].strip() == ""


# ─── ExtractionError taxonomy itself ─


def test_extraction_error_default_kind_is_provider():
    """Default kind='provider' so generic raises don't accidentally end
    up classified as 'no_extractor'/'unconfigured' which have user-facing
    UI consequences."""
    err = ExtractionError("oops")
    assert err.kind == "provider"
    assert str(err) == "oops"


def test_extraction_error_records_explicit_kind():
    err = ExtractionError("bad input", kind="input")
    assert err.kind == "input"
