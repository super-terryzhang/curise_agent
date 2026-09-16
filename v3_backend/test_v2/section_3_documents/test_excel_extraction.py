"""Section 3 — Documents: OpenpyxlExtractor — .xlsx → Markdown tables.

测试目标：
    `OpenpyxlExtractor` 把 .xlsx 的每个 sheet 渲染成 Markdown 表格。
    单 sheet 一个表、多 sheet 多个表用空行隔开。空 .xlsx 抛 ExtractionError。

为什么重要：
    Excel 是邮轮采购系统最常见的报价/库存/订单格式。如果 sheet 渲染丢列，
    下游 product matching 就会拿不到 SKU；如果 header 检测错位，整张表
    就一行 NaN。这两类回归都在历史 bug 列表里。

设计方法：
    用 `openpyxl.Workbook()` 实时合成 .xlsx 字节，断言渲染后的 markdown
    包含 header 行、分隔行、所有数据 cell。多 sheet 用 `## <sheet name>`
    分组所以也直接 grep。
"""

from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from domains.document.extraction.base import ExtractionError
from domains.document.extraction.excel_extractor import OpenpyxlExtractor
from domains.document.extraction.schema import EXTRACTION_SCHEMA_VERSION


_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _build_xlsx(sheets: dict[str, list[list[object]]]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ─── supports() ─


@pytest.mark.parametrize(
    "mime",
    [
        _XLSX_MIME,
        "excel",
        "xlsx",
    ],
)
def test_supports_each_documented_excel_mime(mime):
    """openpyxl only handles modern .xlsx OOXML. Legacy `.xls`
    (`application/vnd.ms-excel`, CFBF binary) is intentionally NOT in
    this whitelist — it routes to `markitdown_extractor` (xlrd backend).
    See `test_v2/section_3_documents/test_xls_routing.py` for the
    routing contract, and R1 in docs/current_progress/2026-06-16/ for
    the bug history (production extraction was failing because this
    list used to claim the legacy mime that openpyxl can't actually
    read)."""
    assert OpenpyxlExtractor().supports(mime) is True


def test_openpyxl_does_not_claim_legacy_xls_mime():
    """Lock-in: `application/vnd.ms-excel` MUST route to markitdown.
    Anyone re-adding the legacy mime here will break the R1 fix that
    unblocked Royal Caribbean .xls uploads. Sibling
    `test_xls_routing.py::test_router_dispatches_legacy_xls_to_markitdown`
    asserts the same invariant at the router layer."""
    assert OpenpyxlExtractor().supports("application/vnd.ms-excel") is False


@pytest.mark.parametrize(
    "mime",
    [
        "application/pdf",
        "image/jpeg",
        "text/csv",
        "application/json",
    ],
)
def test_does_not_claim_non_excel_mimes(mime):
    assert OpenpyxlExtractor().supports(mime) is False


# ─── extract() — single sheet ─


def test_extract_renders_single_sheet_as_markdown_table():
    """Header row → table header. Body rows → data rows. Sheet name → ## heading."""
    blob = _build_xlsx(
        {
            "Inventory": [
                ["sku", "qty"],
                ["BEEF-01", 10],
                ["FISH-02", 5],
            ]
        }
    )
    result = OpenpyxlExtractor().extract(blob, _XLSX_MIME)
    md = result["markdown"]
    assert "## Inventory" in md
    assert "| sku | qty |" in md
    assert "| --- | --- |" in md
    assert "| BEEF-01 | 10 |" in md
    assert "| FISH-02 | 5 |" in md


def test_extract_uses_first_sheet_name_as_title():
    """`title` powers the document detail header — it should be the first
    non-empty sheet's name."""
    blob = _build_xlsx({"PriceList": [["a"], ["1"]]})
    result = OpenpyxlExtractor().extract(blob, _XLSX_MIME)
    assert result["title"] == "PriceList"


def test_extract_counts_non_empty_sheets_as_page_count():
    """page_count = number of sheets with data. Used by the UI's
    `(N sheets)` indicator."""
    blob = _build_xlsx(
        {
            "First": [["a"], ["1"]],
            "Second": [["b"], ["2"]],
            "Third": [["c"], ["3"]],
        }
    )
    result = OpenpyxlExtractor().extract(blob, _XLSX_MIME)
    assert result["page_count"] == 3


def test_extract_sets_schema_version_and_extractor_stats():
    blob = _build_xlsx({"S": [["k", "v"], ["x", "1"]]})
    result = OpenpyxlExtractor().extract(blob, _XLSX_MIME)
    assert result["schema_version"] == EXTRACTION_SCHEMA_VERSION
    assert result["stats"]["extractor"] == "openpyxl"


# ─── extract() — multi-sheet ─


def test_extract_renders_each_sheet_separately():
    """Two sheets → markdown has two `## <name>` headings and both bodies."""
    blob = _build_xlsx(
        {
            "Suppliers": [
                ["name", "country"],
                ["ACME", "JP"],
            ],
            "Products": [
                ["sku", "price"],
                ["A-1", 100],
            ],
        }
    )
    result = OpenpyxlExtractor().extract(blob, _XLSX_MIME)
    md = result["markdown"]
    assert "## Suppliers" in md
    assert "## Products" in md
    assert "| ACME | JP |" in md
    assert "| A-1 | 100 |" in md


def test_extract_fills_missing_header_cells_with_col_n():
    """If the header row has a blank cell, render `col_<idx>` so the
    markdown table stays valid (header count == row count)."""
    blob = _build_xlsx(
        {
            "S": [
                ["sku", "", "price"],
                ["A1", "ignored", 10],
            ]
        }
    )
    result = OpenpyxlExtractor().extract(blob, _XLSX_MIME)
    md = result["markdown"]
    assert "| sku | col_2 | price |" in md


# ─── extract() — error paths ─


def test_extract_raises_empty_for_zero_bytes():
    """Empty input is a user error (no file uploaded), not a parser failure."""
    with pytest.raises(ExtractionError) as exc:
        OpenpyxlExtractor().extract(b"", _XLSX_MIME)
    assert exc.value.kind == "empty"


def test_extract_raises_empty_when_no_sheet_has_data():
    """An xlsx with all-blank sheets has nothing to extract; surface as
    `empty` not `input` so workflow can still store the file."""
    wb = Workbook()
    # Remove the default and make a single blank sheet
    blank = wb.active
    blank.title = "Blank"
    # Add zero rows
    buf = io.BytesIO()
    wb.save(buf)
    blob = buf.getvalue()

    with pytest.raises(ExtractionError) as exc:
        OpenpyxlExtractor().extract(blob, _XLSX_MIME)
    assert exc.value.kind == "empty"


def test_extract_raises_input_on_corrupt_xlsx():
    """Random bytes labelled as xlsx → openpyxl raises; we map to kind='input'."""
    with pytest.raises(ExtractionError) as exc:
        OpenpyxlExtractor().extract(b"this is not a real xlsx", _XLSX_MIME)
    assert exc.value.kind == "input"
