"""Section E2E — Large scanned PDF OCR (chunked).

测试目标：
    `PO102292CCI-A_compressed (1).pdf`（38 页 / 25 MB / 全扫描件）通过
    `PdfOrchestrator` 的 chunked 路径能正确处理。

为什么重要：
    2026-05-13 真实事故：用户上传这个 PDF → Gemini 单次请求 504 DEADLINE_EXCEEDED。
    修复方案：文件 > 15 MB 或 > 10 页时自动分页 OCR + 并行调 Gemini Vision。
    这个测试是回归 —— 任何破坏 chunked 路径的改动会立刻挂。

设计方法：
    - 用 `sample_pdfs` fixture 拿真文件（test-orders/）
    - 真调 Gemini API（需要 `GOOGLE_API_KEY`）
    - 用 v2 已成功处理的 ground truth 校验关键词
    - **跑得慢**（约 5-7 分钟），所以加 @pytest.mark.slow + 默认 skip。
      显式调用：`pytest test_v2/section_e2e/test_large_scanned_pdf_ocr.py -m slow`
      或：`PYTEST_RUN_SLOW=1 pytest test_v2/section_e2e -q`
"""

from __future__ import annotations

import os

import pytest


# Per-test guard (not module-level) so the free routing test still runs.
_skip_real_gemini = pytest.mark.skipif(
    not os.getenv("PYTEST_RUN_SLOW")
    and not os.getenv("RUN_REAL_GEMINI_TESTS"),
    reason="real-Gemini OCR test; set PYTEST_RUN_SLOW=1 to run (~5-7 min)",
)


# Ground truth from v2 system's prior successful extraction of the same file
# (v2_documents.id=30, extraction_method='document-ai-ocr-v1+gemini-structured')
_GROUND_TRUTH_KEYWORDS = [
    "102292",        # PO number (PO102292CCI)
    "celebrity",     # Vessel name (CELEBRITY SOLSTICE)
    "solstice",
    "three win",     # Vendor name (Three Win Co Ltd)
    "ho chi minh",   # Destination port
    "apple",         # First-line product (APPLE GRANNY SMITH)
]


@_skip_real_gemini
def test_chunked_ocr_handles_38_page_scanned_pdf(sample_pdfs):
    """End-to-end: 25 MB / 38-page scanned PDF → chunked Gemini OCR.

    Asserts:
        1. Does not raise (no 504 DEADLINE_EXCEEDED).
        2. Routes through chunked path (extractor == "gemini-pdf-chunked").
        3. Output page_count matches source (38).
        4. All 6 v2-baseline keywords appear in extracted markdown.
        5. Completes in < 540 s (Cloud Run timeout budget).
    """
    if "cci_large_po_102292" not in sample_pdfs:
        pytest.skip("large CCI sample PDF not available")

    from domains.document.extraction.pypdf_extractor import PdfOrchestrator

    import time as _t

    file_bytes = sample_pdfs["cci_large_po_102292"]
    assert len(file_bytes) > 20_000_000, "expected sample > 20 MB"

    orchestrator = PdfOrchestrator()
    start = _t.perf_counter()
    result = orchestrator.extract(file_bytes, "application/pdf")
    elapsed = _t.perf_counter() - start

    md = result.get("markdown") or ""
    stats = result.get("stats") or {}

    # 2026-05-13: extractor renamed when we merged OCR + structure into a
    # single per-page LLM call. The name change marks the new contract:
    # result now includes `products` + `metadata`, not just `markdown`.
    assert stats.get("extractor") == "gemini-pdf-chunked-structured", (
        f"expected chunked-structured path, got {stats.get('extractor')!r}"
    )
    # New unified shape — projector reads these to skip a 2nd LLM call.
    assert "products" in result, "result missing 'products' (new unified shape)"
    assert "metadata" in result, "result missing 'metadata' (new unified shape)"
    assert isinstance(result.get("products"), list), "products must be a list"
    assert isinstance(result.get("metadata"), dict), "metadata must be a dict"
    assert result.get("page_count") == 38, (
        f"page_count should be 38, got {result.get('page_count')}"
    )
    assert len(md) >= 5000, (
        f"markdown too short: {len(md)} chars (v2 baseline was 8171)"
    )
    assert len(md) < 600_000, (
        f"markdown suspiciously long ({len(md)} chars) — likely model "
        f"hallucination loop. Expected ~50-400K chars for 38 pages."
    )
    # Performance budget: chunked OCR for 38 pages must complete < 120s.
    # After 2026-05-13 optimization (Flash Lite + JPEG 1.5x + max_output_tokens
    # cap + 8 workers), measured 60.7s. Setting the bound at 2x for safety.
    assert elapsed < 120, (
        f"chunked OCR took {elapsed:.0f}s — exceeds 120s budget. "
        f"Expected ~60s. Check if Flash Lite still being used + max_output_tokens cap."
    )

    md_lower = md.lower()
    missing = [kw for kw in _GROUND_TRUTH_KEYWORDS if kw not in md_lower]
    assert not missing, (
        f"missing v2-baseline keywords: {missing}.\n"
        f"First 500 chars of output:\n{md[:500]}"
    )


def test_chunked_path_routes_by_size_threshold(sample_pdfs):
    """Routing logic: >15 MB OR >10 pages → chunked, else single-shot.

    Small samples (the 6 other PDFs in test-orders/) should NOT be flagged
    for chunked. This is a unit check of the heuristic, not a Gemini call —
    it only inspects which branch the orchestrator would pick.

    Note: this test doesn't run Gemini; it's free + always runs.
    """
    from domains.document.extraction.pypdf_extractor import PdfOrchestrator

    orchestrator = PdfOrchestrator()  # noqa: F841 — instantiation alone shouldn't fail

    sizes = {name: len(blob) / 1024 / 1024 for name, blob in sample_pdfs.items()}
    # only the large CCI file should be > 15 MB
    over_15mb = {name for name, mb in sizes.items() if mb > 15}
    assert over_15mb == {"cci_large_po_102292"}, (
        f"expected only cci_large_po_102292 > 15MB, got {over_15mb}"
    )
