"""Section 4 — orders: PO extraction text-path routing (v46+).

测试目标：
    Pin v46 的核心契约：
      - 文本型 PDF (chars/page >= 50) 走 pypdfium2 + text 路径
      - 扫描型 PDF (chars/page < 50) 走 Vision 路径作 fallback
      - 路径决策由 `_extract_pdf_text_via_pypdfium2` 提供数据，
        `extract_po_structure` 根据该数据分流
    Prod 2026-05-21 order #104 因 Vision 跨页错位导致客户报告
    "mango 消失"。这个测试用纯函数 (pypdfium2 度量) 验证路径决策本身
    是稳定的，不依赖 LLM API。

为什么必须有：
    一旦 pypdfium2 升级 / Cloud Run 镜像换底层 / 阈值需要调，这条测试
    保证我们看到行为变化的第一时间。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from domains.orders._llm_extractor import _extract_pdf_text_via_pypdfium2


def _make_minimal_text_pdf(text_lines: list[str]) -> bytes:
    """Generate a tiny one-page text PDF in memory using pypdfium2."""
    import pypdfium2 as pdfium
    # We can't easily synthesize a PDF without an external lib, so just
    # use a precomputed minimal PDF for tests.
    raise NotImplementedError  # See fixtures.


def test_pypdfium2_helper_empty_pdf_returns_zeros() -> None:
    """Empty bytes → graceful degrade, not exception."""
    text, cpp = _extract_pdf_text_via_pypdfium2(b"")
    assert text == ""
    assert cpp == 0


def test_pypdfium2_helper_corrupt_pdf_returns_zeros() -> None:
    """Corrupt bytes → no exception, fall through to Vision."""
    text, cpp = _extract_pdf_text_via_pypdfium2(b"not a real pdf")
    assert text == ""
    assert cpp == 0


@pytest.mark.skipif(
    not Path("/tmp/pdftest/order_104.pdf").exists(),
    reason="prod sample PDFs not present; run benchmark first to populate",
)
def test_pypdfium2_helper_text_based_pdf_passes_threshold() -> None:
    """v46 threshold: a real cruise PO PDF must measure >= 50 chars/page.

    Empirical baseline (prod 2026-05-21):
      - order 91:  2062 chars/page
      - order 104: 3824 chars/page
    Anything under 50 would mean pypdfium2 broke or the test PDF was
    swapped for a scanned image — both are real failure modes worth
    catching."""
    with open("/tmp/pdftest/order_104.pdf", "rb") as f:
        bytes_ = f.read()
    text, cpp = _extract_pdf_text_via_pypdfium2(bytes_)
    assert cpp >= 50, f"order 104 must route to text path; cpp={cpp}"
    # ROMAINE description must be in the extracted text — if not, pypdfium2
    # version changed or page-spanning extraction broke at the lib level.
    assert "LETTUCE ROMAINE 24CT/40LB" in text


@pytest.mark.skipif(
    not Path("/tmp/pdftest/order_102.pdf").exists(),
    reason="prod sample PDFs not present",
)
def test_pypdfium2_helper_scanned_pdf_falls_below_threshold() -> None:
    """Scanned PDFs (image-only) measure ~0 chars/page → Vision fallback.

    Prod sample order 102 = 38-page iLovePDF-compressed image PO,
    pypdfium2 extracts 1 char/page (just page breaks). Our routing
    must NOT send these to the text path."""
    with open("/tmp/pdftest/order_102.pdf", "rb") as f:
        bytes_ = f.read()
    text, cpp = _extract_pdf_text_via_pypdfium2(bytes_)
    assert cpp < 50, (
        f"order 102 is scanned, cpp must be < 50 to route to Vision; got {cpp}"
    )
