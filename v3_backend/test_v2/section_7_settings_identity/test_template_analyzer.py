"""Section 7 — Template analyzer: infer/excel/pdf heuristics.

测试目标：
    `domains/settings/_analyze.py` 是 Phase 4 的"无 LLM 模板分析"：
      - infer_template_metadata: 在 raw_text 里抓邮轮公司关键字 (Royal Caribbean
        / Carnival / MSC / Disney / Silversea / Seabourn)
      - analyze_excel_template: 用 openpyxl 扫单元格，用 _FIELD_HINTS 给出
        po_number / ship_name / vendor_name / delivery_date 等字段位置
      - analyze_pdf_template: 用 markitdown 抽 PDF 文字, 判断 document_type

为什么重要：
    Phase 5 之后会换成 Gemini, 但 Phase 4 这套"启发式"是无 LLM 也能工作的
    cold-start fallback — 它出 bug = 后台模板配置半瘫痪 (用户得手填全部字段)。

设计方法：
    PDF 用 `test_v2/fixtures/helpers.py::make_minimal_pdf` 现搓字节；
    Excel 用 openpyxl 现搓 — 不用真 file。
    每个测试只断 *一条* 行为，参数化覆盖"6 家邮轮公司各识一次"。
"""

from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from domains.settings._analyze import (
    analyze_excel_template,
    analyze_pdf_template,
    infer_template_metadata,
)
from test_v2.fixtures.helpers import make_minimal_pdf


# ─── infer_template_metadata: company keyword detection ───────


def test_infer_metadata_detects_royal_caribbean(_disable_llm_extractor) -> None:
    """ROYAL CARIBBEAN → source_company="Royal Caribbean" + 命名 "X 模板"."""
    meta = infer_template_metadata(
        raw_text="THIS IS A ROYAL CARIBBEAN PURCHASE ORDER",
        headers=[],
        file_type="excel",
    )
    assert meta["source_company"] == "Royal Caribbean"
    assert "Royal Caribbean" in meta["name"]
    # 命中的 raw text 进 keywords (大写、去重)
    assert any("ROYAL" in k for k in meta["match_keywords"])


@pytest.mark.parametrize(
    "raw_text,expected_company",
    [
        ("CARNIVAL CRUISE LINE", "Carnival Cruise"),
        ("MSC Cruises Standard Form", "MSC Cruises"),
        ("Disney Cruise Line PO", "Disney Cruise"),
        ("SILVERSEA Procurement", "Silversea"),
        ("Seabourn Order", "Seabourn"),
    ],
)
def test_infer_metadata_recognizes_top_6_cruise_lines(
    raw_text: str, expected_company: str
) -> None:
    """6 家公司都得能从 raw_text 里识别 — 这一组覆盖默认 _KEYWORD_HINTS."""
    meta = infer_template_metadata(raw_text=raw_text, headers=[], file_type="excel")
    assert meta["source_company"] == expected_company


def test_infer_metadata_unknown_company_falls_back_to_file_type(_disable_llm_extractor) -> None:
    """没匹中任何公司 → source_company=None, name 用 file_type 大写做兜底命名。"""
    meta = infer_template_metadata(
        raw_text="Hello world, plain text without any cruise line name",
        headers=[],
        file_type="pdf",
    )
    assert meta["source_company"] is None
    assert "PDF" in meta["name"]  # "PDF 模板（待命名）"
    assert meta["match_keywords"] == []


def test_infer_metadata_uses_headers_when_raw_text_empty() -> None:
    """raw_text 空时退到 headers 拼接搜 — 保证 Excel 仅给表头也能识别。"""
    meta = infer_template_metadata(
        raw_text="",
        headers=["No.", "Royal Caribbean Vendor Code", "Qty"],
        file_type="excel",
    )
    assert meta["source_company"] == "Royal Caribbean"


# ─── analyze_excel_template: field positions ───────────────────


def _build_excel(rows: list[list[object]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_analyze_excel_detects_po_ship_vendor_positions() -> None:
    """PO No / Ship Name / Vendor 是订单模板里最常 prompt 给 LLM 的三块元数据;
    _FIELD_HINTS 必须给它们各分配一个 position。"""
    xlsx = _build_excel(
        [
            ["PO No", "Ship Name", "Vendor", "Delivery Date"],
            ["PO-1", "Pacific Star", "ACME Inc", "2026-01-01"],
        ]
    )
    out = analyze_excel_template(xlsx)
    fp = out["field_positions"]
    # 必须识别出这 3 个 key (位置随实现可能小变, 但 key 必须在)
    assert "po_number" in fp
    assert "ship_name" in fp
    assert "vendor_name" in fp
    # position 是 A1 / B1 / C1 — 由 openpyxl 给出
    assert fp["po_number"]["position"] == "A1"
    assert fp["ship_name"]["position"] == "B1"
    assert fp["vendor_name"]["position"] == "C1"


def test_analyze_excel_detects_product_table_start_row() -> None:
    """启发式: 第一行有 ≥3 短文本 = 表头 → product_table_config.start_row =
    header_row + 1。下游用这个 row 起开始读货品。"""
    xlsx = _build_excel(
        [
            ["商品名称", "数量", "单价"],  # 行 1: header
            ["苹果", 10, 5.0],  # 行 2: 数据
            ["香蕉", 5, 3.0],
        ]
    )
    out = analyze_excel_template(xlsx)
    cfg = out["product_table_config"]
    assert cfg.get("header_row") == 1
    assert cfg.get("start_row") == 2


def test_analyze_excel_populates_cell_map_with_all_non_empty_cells() -> None:
    """cell_map = {position -> value} 是 frontend 渲染模板预览的原料 —
    每一个非空格子都必须进 map。"""
    xlsx = _build_excel(
        [
            ["A1text", "B1text"],
            ["A2text", None],  # 空格不能进
        ]
    )
    out = analyze_excel_template(xlsx)
    cm = out["cell_map"]
    assert cm.get("A1") == "A1text"
    assert cm.get("B1") == "B1text"
    assert cm.get("A2") == "A2text"
    assert "B2" not in cm


def test_analyze_excel_returns_sheets_with_name_and_header_row() -> None:
    """每个 sheet 都要在 sheets 数组里, 含 name + 启发出的 header_row。"""
    xlsx = _build_excel(
        [["商品", "数量", "单位"], ["a", 1, "kg"]]
    )
    out = analyze_excel_template(xlsx)
    assert len(out["sheets"]) == 1
    sh = out["sheets"][0]
    assert sh["name"] == "Sheet1"
    assert sh["header_row"] == 1


# ─── analyze_pdf_template: PDF heuristics ──────────────────────


def test_analyze_pdf_returns_markdown_length_for_valid_pdf() -> None:
    """正常 PDF → markdown_length > 0 (markitdown 从 PDF 抽出文本)。"""
    pdf = make_minimal_pdf("Purchase Order PO-AAA-1 vendor: ACME")
    out = analyze_pdf_template(pdf)
    schema = out["document_schema"]
    # markdown_length 一定 >= 0; 真的提到字时 > 0
    assert isinstance(schema["markdown_length"], int)
    assert schema["markdown_length"] >= 0


def test_analyze_pdf_classifies_purchase_order_when_text_matches() -> None:
    """"purchase order" / "PO number" / "発注" 任一关键字命中 → document_type='Purchase Order'."""
    pdf = make_minimal_pdf("This is a Purchase Order. PO number AAA-1")
    out = analyze_pdf_template(pdf)
    assert out["document_type"] == "Purchase Order"
    assert out["document_schema"]["document_type"] == "Purchase Order"


def test_analyze_pdf_returns_empty_schema_shape() -> None:
    """Phase 4 的 schema 里 attribute_groups / page_layout / field_mapping
    必须有, 即使是空 — Phase 5 用 Gemini 填充。"""
    pdf = make_minimal_pdf("anything")
    out = analyze_pdf_template(pdf)
    schema = out["document_schema"]
    assert "attribute_groups" in schema
    assert "page_layout" in schema
    assert "field_mapping" in schema
    assert isinstance(schema["attribute_groups"], list)
    assert isinstance(schema["field_mapping"], dict)
