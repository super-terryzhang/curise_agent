"""Section 3 — Documents: PdfOrchestrator — born-digital + Gemini fallback.

测试目标：
    `PdfOrchestrator` 是 PDF 的注册 Extractor。它做的事：
      1. 先调 `PyPDFiumExtractor` 抽文本层。
      2. 若 markdown 非空 → 直接返回（绝大多数情况）。
      3. 若 markdown 为空但 PDF 解析 OK（说明是扫描件）→ 调 Gemini OCR。
      4. 若解析就失败（input/empty/...）→ 让真错抛回去，不掩盖。

为什么重要：
    - 这条 fallback 路径是 PDF 系统里唯一会真的烧 LLM 钱的入口。
      错放：把每一份 born-digital PDF 都送去 OCR 会撞 quota；
      错挡：扫描件直接返回空 markdown 就让用户文档堆里全是"已抽取但是空"。
    - 缺 GOOGLE_API_KEY 时必须明确报 `unconfigured` 而不是默默成功 —
      workflow 据此设置 processing_error 告知 admin。
    - 真实 parser 错（损坏 PDF）必须直接抛 input，不能 fallback —
      否则成本爆炸。

设计方法：
    所有外部依赖（Gemini Client、Part 工厂、API key/model 读取器）都是
    构造参数。注入 fake client，断言它是否被调用以及用什么 prompt。
"""

from __future__ import annotations

from typing import Any

import pytest

from domains.document.extraction.base import ExtractionError
from domains.document.extraction.pypdf_extractor import (
    PdfOrchestrator,
    PyPDFiumExtractor,
)
from test_v2.fixtures.helpers import make_minimal_pdf


# ─── Fakes — replace the Gemini SDK without touching google.genai ─


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeClient:
    """Minimal stand-in for `genai.Client` with a `.models.generate_content`."""

    def __init__(self, response_text: str = "OCR’d page content") -> None:
        self._response_text = response_text
        self.calls: list[dict[str, Any]] = []

        class _Models:
            def __init__(_self, parent: "_FakeClient") -> None:
                _self._parent = parent

            def generate_content(_self, *, model: str, contents: list[Any]):
                _self._parent.calls.append({"model": model, "contents": contents})
                return _FakeResponse(_self._parent._response_text)

        self.models = _Models(self)


def _client_factory(response_text: str = "Fallback OCR output"):
    """Returns a factory that yields the same _FakeClient for inspection."""
    shared = _FakeClient(response_text)

    def factory(api_key: str) -> _FakeClient:
        return shared

    return factory, shared


def _stub_pdf_part(file_bytes: bytes) -> dict[str, Any]:
    """Tests don't need a real `Part`; a dict is fine since the fake client
    never inspects it."""
    return {"part": True, "size": len(file_bytes)}


# ─── supports() ─


def test_supports_delegates_to_text_extractor():
    """The orchestrator must accept exactly the same MIMEs that
    PyPDFiumExtractor accepts — single source of truth."""
    orch = PdfOrchestrator()
    assert orch.supports("application/pdf") is True
    assert orch.supports("pdf") is True
    assert orch.supports("image/jpeg") is False


# ─── Born-digital → no Gemini call ─


def test_born_digital_pdf_returns_text_layer_and_never_calls_gemini():
    """The cheap path must short-circuit before any LLM cost."""
    factory, shared_client = _client_factory("should never appear")
    orch = PdfOrchestrator(
        client_factory=factory,
        pdf_part_factory=_stub_pdf_part,
        api_key_loader=lambda: "fake-key",
        model_loader=lambda: "gemini-test",
    )
    pdf = make_minimal_pdf("Born digital content")
    result = orch.extract(pdf, "application/pdf")
    assert "Born digital content" in result["markdown"]
    assert result["stats"]["extractor"] == "pypdfium2"
    assert shared_client.calls == []  # zero Gemini calls


# ─── Scanned (empty text-layer) → Gemini fallback fires ─


class _EmptyTextExtractor(PyPDFiumExtractor):
    """Pretends every PDF parses OK but with no text — simulating a scan."""

    def extract(self, file_bytes, mime_type):  # type: ignore[override]
        return {
            "schema_version": "2.0",
            "language": None,
            "page_count": 1,
            "title": None,
            "markdown": "",  # empty → orchestrator falls through
            "stats": {
                "extractor": "pypdfium2",
                "elapsed_seconds": 0.01,
                "input_tokens": None,
                "output_tokens": None,
                "finish_reason": None,
                "truncated": False,
            },
        }


def test_scanned_pdf_triggers_gemini_fallback():
    factory, shared = _client_factory("# Page 1\n\nScanned text here")
    orch = PdfOrchestrator(
        text_extractor=_EmptyTextExtractor(),
        client_factory=factory,
        pdf_part_factory=_stub_pdf_part,
        api_key_loader=lambda: "fake-key",
        model_loader=lambda: "gemini-2.5-flash",
    )
    result = orch.extract(b"%PDF-fake", "application/pdf")
    assert "Scanned text here" in result["markdown"]
    assert result["stats"]["extractor"] == "gemini-pdf"
    # Gemini was called exactly once with the configured model
    assert len(shared.calls) == 1
    assert shared.calls[0]["model"] == "gemini-2.5-flash"


def test_scanned_pdf_picks_up_first_heading_as_title():
    factory, _ = _client_factory("# Invoice #INV-42\n\nbody")
    orch = PdfOrchestrator(
        text_extractor=_EmptyTextExtractor(),
        client_factory=factory,
        pdf_part_factory=_stub_pdf_part,
        api_key_loader=lambda: "fake-key",
    )
    result = orch.extract(b"%PDF-fake", "application/pdf")
    assert result["title"] == "Invoice #INV-42"


# ─── Failure modes ─


def test_scanned_pdf_without_api_key_raises_unconfigured():
    """Missing GOOGLE_API_KEY must surface as kind='unconfigured' so the
    workflow stores the file + sets processing_error (NOT a generic
    provider error that pages someone)."""
    factory, _ = _client_factory()
    orch = PdfOrchestrator(
        text_extractor=_EmptyTextExtractor(),
        client_factory=factory,
        pdf_part_factory=_stub_pdf_part,
        api_key_loader=lambda: "",  # ← no key
    )
    with pytest.raises(ExtractionError) as exc:
        orch.extract(b"%PDF-fake", "application/pdf")
    assert exc.value.kind == "unconfigured"


def test_empty_bytes_raises_without_invoking_gemini():
    """Empty input is a user error from PyPDFiumExtractor — must propagate
    verbatim, NOT be misinterpreted as 'scanned and needs OCR'."""
    factory, shared = _client_factory()
    orch = PdfOrchestrator(
        client_factory=factory,
        pdf_part_factory=_stub_pdf_part,
        api_key_loader=lambda: "fake-key",
    )
    with pytest.raises(ExtractionError) as exc:
        orch.extract(b"", "application/pdf")
    assert exc.value.kind == "empty"
    assert shared.calls == []


def test_corrupt_pdf_raises_input_without_invoking_gemini():
    """Real parser error — don't waste LLM money trying to OCR garbage."""
    factory, shared = _client_factory()
    orch = PdfOrchestrator(
        client_factory=factory,
        pdf_part_factory=_stub_pdf_part,
        api_key_loader=lambda: "fake-key",
    )
    with pytest.raises(ExtractionError) as exc:
        orch.extract(b"not a real pdf at all", "application/pdf")
    assert exc.value.kind == "input"
    assert shared.calls == []


def test_gemini_returning_empty_text_raises_empty():
    """If Gemini OCR comes back empty, surface as kind='empty' (NOT silent
    success with markdown='')."""
    factory, _ = _client_factory("")  # empty response
    orch = PdfOrchestrator(
        text_extractor=_EmptyTextExtractor(),
        client_factory=factory,
        pdf_part_factory=_stub_pdf_part,
        api_key_loader=lambda: "fake-key",
    )
    with pytest.raises(ExtractionError) as exc:
        orch.extract(b"%PDF-fake", "application/pdf")
    assert exc.value.kind == "empty"


def test_gemini_sdk_failure_raises_provider():
    """A client_factory that raises must surface as kind='provider' so
    monitoring distinguishes upstream API issues from user input errors."""

    def broken_factory(api_key: str):
        raise RuntimeError("network down")

    orch = PdfOrchestrator(
        text_extractor=_EmptyTextExtractor(),
        client_factory=broken_factory,
        pdf_part_factory=_stub_pdf_part,
        api_key_loader=lambda: "fake-key",
    )
    with pytest.raises(ExtractionError) as exc:
        orch.extract(b"%PDF-fake", "application/pdf")
    assert exc.value.kind == "provider"
