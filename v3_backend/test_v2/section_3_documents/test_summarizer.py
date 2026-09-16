"""Section 3 — Documents: summarizer — LLM-driven {summary, tags, language}.

测试目标：
    `summarize_document` 拿到抽取好的 markdown → 调一次 Gemini → 返回
    `{summary, tags, language}` 用于详情页 + agent 检索。

为什么重要：
    - 这个函数运行在 workflow 后半段，每份成功抽取的文档都会跑一次。
    - 失败语义是关键：缺 API key / 空 markdown → 静默返回 None（不能 raise
      让整张文档变 status=error）；非 JSON / 空 tags / API 报错 → raise
      SummarizerError 让 workflow catch + log。
    - 长文档要被截到 _MAX_MARKDOWN_CHARS 才不超 Gemini Flash 输入上限。
    - tag 归一化必须把脏 LLM 输出（"Beef Supplier", "BEEF_SUPPLIER"）合并到
      "beef-supplier"，否则 sidebar 全是重复 chip。

设计方法：
    `summarizer._call_gemini` 是一个 module-level helper —— monkeypatch 它来
    模拟各种 LLM 回复（成功 JSON / 空字符串 / 非 JSON / 抛异常）。这是注入
    Gemini 的最干净办法（没用 factory pattern）。
"""

from __future__ import annotations

import json

import pytest

from domains.document import summarizer
from domains.document.summarizer import (
    SummarizerError,
    _normalize_tags,
    _parse_response,
    summarize_document,
)


# ─── Guard rails: when summarization should be SKIPPED ─


def test_returns_none_when_api_key_is_missing(monkeypatch):
    """No GOOGLE_API_KEY → return None silently. The workflow then skips
    setting summary/tags. This intentionally does NOT raise so the upload
    doesn't get marked as failed just because the admin didn't configure
    Gemini yet."""
    monkeypatch.setattr(summarizer.settings, "GOOGLE_API_KEY", "")
    assert summarize_document("anything") is None


def test_returns_none_when_markdown_is_empty(monkeypatch):
    """Empty markdown is what we get from scanned-PDF-without-Gemini. The
    summarizer shouldn't waste an LLM call to summarize nothing."""
    monkeypatch.setattr(summarizer.settings, "GOOGLE_API_KEY", "fake-key")
    # Make sure no Gemini call could possibly succeed if we slipped through
    monkeypatch.setattr(
        summarizer,
        "_call_gemini",
        lambda *a, **kw: pytest.fail("should not be called"),
    )
    assert summarize_document("") is None
    assert summarize_document("   \n\t  ") is None


# ─── Happy path: LLM returns valid JSON ─


def test_returns_summary_tags_language_on_happy_path(monkeypatch):
    captured = {}

    def fake_call(api_key, model, prompt):
        captured["api_key"] = api_key
        captured["model"] = model
        captured["prompt"] = prompt
        return json.dumps(
            {
                "summary": "A purchase order for 3 SKUs from ACME Foods.",
                "tags": ["acme-foods", "purchase-order", "frozen-meat"],
                "language": "en",
            }
        )

    monkeypatch.setattr(summarizer.settings, "GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(summarizer, "_call_gemini", fake_call)

    result = summarize_document(
        "## PO 12345\nLine 1: BEEF-01\n", filename="po.pdf", file_type="pdf"
    )

    assert result == {
        "summary": "A purchase order for 3 SKUs from ACME Foods.",
        "tags": ["acme-foods", "purchase-order", "frozen-meat"],
        "language": "en",
    }
    # The key + prompt actually flow through
    assert captured["api_key"] == "test-key"
    assert "PO 12345" in captured["prompt"]
    assert "po.pdf" in captured["prompt"]


def test_prompt_carries_document_context(monkeypatch):
    """filename + file_type + doc_type all show up in the prompt so the
    LLM can write a domain-aware summary."""
    captured = {}

    def fake_call(api_key, model, prompt):
        captured["prompt"] = prompt
        return json.dumps(
            {
                "summary": "ok",
                "tags": ["a", "b", "c"],
                "language": "en",
            }
        )

    monkeypatch.setattr(summarizer.settings, "GOOGLE_API_KEY", "k")
    monkeypatch.setattr(summarizer, "_call_gemini", fake_call)

    summarize_document(
        "Some markdown body",
        filename="invoice-42.pdf",
        file_type="pdf",
        doc_type="purchase_order",
    )

    assert "invoice-42.pdf" in captured["prompt"]
    assert "pdf" in captured["prompt"]
    assert "purchase_order" in captured["prompt"]


# ─── Truncation for very long markdown ─


def test_long_markdown_is_truncated_with_explicit_note(monkeypatch):
    """Past _MAX_MARKDOWN_CHARS we truncate AND append a marker so the LLM
    knows the tail was cut — this prevents the summary from claiming the
    document is shorter than it actually is."""
    captured = {}

    def fake_call(api_key, model, prompt):
        captured["prompt"] = prompt
        return json.dumps(
            {
                "summary": "summary here",
                "tags": ["a", "b", "c"],
                "language": "en",
            }
        )

    monkeypatch.setattr(summarizer.settings, "GOOGLE_API_KEY", "k")
    monkeypatch.setattr(summarizer, "_call_gemini", fake_call)

    long_md = "X" * (summarizer._MAX_MARKDOWN_CHARS + 5000)
    summarize_document(long_md)

    # Note injection happens AFTER truncation
    assert "(truncated for summarization)" in captured["prompt"]
    # And the prompt body isn't carrying the full _MAX + 5000 verbatim
    # (i.e. truncation actually fired)
    x_count = captured["prompt"].count("X")
    assert x_count <= summarizer._MAX_MARKDOWN_CHARS


def test_short_markdown_is_not_truncated(monkeypatch):
    """Below the cap, no truncation note should appear — otherwise short
    docs would mysteriously claim to be truncated."""
    captured = {}

    def fake_call(api_key, model, prompt):
        captured["prompt"] = prompt
        return json.dumps(
            {
                "summary": "summary",
                "tags": ["a", "b", "c"],
                "language": "en",
            }
        )

    monkeypatch.setattr(summarizer.settings, "GOOGLE_API_KEY", "k")
    monkeypatch.setattr(summarizer, "_call_gemini", fake_call)

    summarize_document("brief markdown")
    assert "truncated for summarization" not in captured["prompt"]


# ─── Hard failures (LLM call + parse) ─


def test_non_json_response_raises_summarizer_error(monkeypatch):
    """Gemini schema mode usually returns JSON, but if it doesn't we must
    raise — silently storing garbage in `summary` would be worse."""
    monkeypatch.setattr(summarizer.settings, "GOOGLE_API_KEY", "k")
    monkeypatch.setattr(
        summarizer, "_call_gemini", lambda *a, **kw: "not even close to json"
    )

    with pytest.raises(SummarizerError, match="non-JSON"):
        summarize_document("some markdown")


def test_missing_summary_field_raises_summarizer_error(monkeypatch):
    monkeypatch.setattr(summarizer.settings, "GOOGLE_API_KEY", "k")
    monkeypatch.setattr(
        summarizer,
        "_call_gemini",
        lambda *a, **kw: json.dumps({"summary": "", "tags": ["a", "b", "c"]}),
    )

    with pytest.raises(SummarizerError, match="summary"):
        summarize_document("body")


def test_no_usable_tags_raises_summarizer_error(monkeypatch):
    """If every tag normalizes to empty (punctuation only), surface as
    SummarizerError so the caller knows the LLM output was unusable."""
    monkeypatch.setattr(summarizer.settings, "GOOGLE_API_KEY", "k")
    monkeypatch.setattr(
        summarizer,
        "_call_gemini",
        lambda *a, **kw: json.dumps(
            {"summary": "some text", "tags": ["!!!", "***", "   "]}
        ),
    )

    with pytest.raises(SummarizerError, match="tags"):
        summarize_document("body")


def test_gemini_sdk_failure_bubbles_as_summarizer_error(monkeypatch):
    """When the real _call_gemini fails it raises SummarizerError. The same
    must come out of summarize_document — workflow catches this type only."""

    def raise_for_test(*a, **kw):
        raise SummarizerError("Gemini API call failed: network down")

    monkeypatch.setattr(summarizer.settings, "GOOGLE_API_KEY", "k")
    monkeypatch.setattr(summarizer, "_call_gemini", raise_for_test)

    with pytest.raises(SummarizerError, match="network down"):
        summarize_document("body")


# ─── _parse_response: direct unit tests ─


def test_parse_response_normalizes_language_to_lowercase():
    raw = json.dumps(
        {"summary": "x", "tags": ["a", "b", "c"], "language": "EN"}
    )
    result = _parse_response(raw)
    assert result["language"] == "en"


def test_parse_response_treats_blank_language_as_none():
    raw = json.dumps(
        {"summary": "x", "tags": ["a", "b", "c"], "language": "   "}
    )
    result = _parse_response(raw)
    assert result["language"] is None


def test_parse_response_handles_missing_language_field():
    """`language` is optional — absent key → None."""
    raw = json.dumps({"summary": "x", "tags": ["a", "b", "c"]})
    assert _parse_response(raw)["language"] is None


# ─── _normalize_tags: direct unit tests ─


def test_normalize_tags_dedupes_case_insensitively():
    out = _normalize_tags(["Beef", "BEEF", "beef-supplier"])
    assert out == ["beef", "beef-supplier"]


def test_normalize_tags_converts_spaces_and_underscores_to_hyphens():
    out = _normalize_tags(["beef supplier", "celebrity_cruise"])
    assert out == ["beef-supplier", "celebrity-cruise"]


def test_normalize_tags_drops_non_string_entries():
    """A defensive guard so a partial LLM response (e.g. tags array
    containing nulls) doesn't crash with TypeError."""
    out = _normalize_tags(["a", None, 42, "b"])  # type: ignore[list-item]
    assert out == ["a", "b"]


def test_normalize_tags_caps_at_max_tag_count():
    """Prevents an overlong tag list from polluting the chips UI."""
    out = _normalize_tags([f"tag-{i}" for i in range(20)])
    assert len(out) == summarizer._TAG_MAX
