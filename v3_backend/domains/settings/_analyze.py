"""Template analysis — Phase 4 simplified version (no Gemini).

Phase 4 ships deterministic, no-LLM analysis:
- Excel: openpyxl finds non-empty cells, returns position map and headers
- PDF: PyPDFium extracts blocks, surfaces detected keywords as inferred metadata

Phase 5 plugs in Gemini for richer field-mapping. The HTTP layer talks to
this module, not to extractor internals.
"""

from __future__ import annotations

import logging
import re
from io import BytesIO
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from domains.document import extract

logger = logging.getLogger(__name__)


# ─── Inference (called by /order-templates/infer) ────────────


_KEYWORD_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bROYAL\s+CARIBBEAN\b", re.IGNORECASE), "Royal Caribbean"),
    (re.compile(r"\bCARNIVAL\b", re.IGNORECASE), "Carnival Cruise"),
    (re.compile(r"\bMSC\s+CRUISES?\b", re.IGNORECASE), "MSC Cruises"),
    (re.compile(r"\bDISNEY\s+CRUISE\b", re.IGNORECASE), "Disney Cruise"),
    (re.compile(r"\bSILVERSEA\b", re.IGNORECASE), "Silversea"),
    (re.compile(r"\bSEABOURN\b", re.IGNORECASE), "Seabourn"),
]


def infer_template_metadata(*, raw_text: str, headers: list[str], file_type: str) -> dict[str, Any]:
    """Deterministic: scan known cruise lines + return fingerprint of headers.

    Phase 5 will replace this with a Gemini structured call. Until then, the
    keyword scan covers the top 6 cruise lines and produces something
    sensible for everything else (fall back to file_type-based name).
    """
    text = (raw_text or "").strip() or " ".join(headers or [])
    source_company: str | None = None
    keywords: list[str] = []

    for pattern, name in _KEYWORD_HINTS:
        if pattern.search(text):
            source_company = name
            # Use the raw matched text as a keyword (deduped, lowercased)
            keywords.extend({m.upper() for m in pattern.findall(text)})
            break

    name = f"{source_company} 模板" if source_company else f"{file_type.upper()} 模板（待命名）"
    return {
        "name": name,
        "source_company": source_company,
        "match_keywords": list(dict.fromkeys(keywords)),  # preserve order, dedupe
        "notes": "Phase 4 deterministic infer — Phase 5 will use Gemini",
    }


# ─── PDF analysis (called by /order-templates/analyze-pdf) ────


def analyze_pdf_template(content: bytes) -> dict[str, Any]:
    """Run the universal extractor against a PDF and surface a minimal schema.

    Phase 4 returns a schema with the document_type guessed from text and an
    empty `field_mapping` — Phase 5 plugs in Gemini for real semantic mapping.
    """
    extracted = extract(content, mime_type="application/pdf")
    title = extracted.get("title")
    text_parts: list[str] = []
    if title:
        text_parts.append(str(title))
    md = extracted.get("markdown")
    if md:
        text_parts.append(str(md))
    text = "\n".join(text_parts)

    # Cheap guess at document type
    document_type = (
        "Purchase Order"
        if re.search(r"purchase\s+order|PO\s+number|発注", text, re.IGNORECASE)
        else "Unknown"
    )

    schema: dict[str, Any] = {
        "document_type": document_type,
        "title": title,
        "page_count": extracted.get("page_count"),
        "markdown_length": len(md or ""),
        "attribute_groups": [],
        "page_layout": [],
        "field_mapping": {},
    }
    return {
        "document_schema": schema,
        "document_type": document_type,
        "sample_file_url": "",  # caller fills this in after upload
        "timing": extracted.get("stats", {}),
    }


# ─── Excel analysis (called by /supplier-templates/analyze) ────


def analyze_excel_template(content: bytes) -> dict[str, Any]:
    """Return cell positions + heuristic field positions for an Excel template.

    Phase 4 ships pure-openpyxl analysis — fast, deterministic. Phase 5
    layers Gemini on top to map cells to standard field keys.
    """
    wb = load_workbook(BytesIO(content), read_only=True, data_only=True)
    cell_map: dict[str, Any] = {}
    field_positions: dict[str, dict[str, Any]] = {}
    product_table_config: dict[str, Any] = {}

    try:
        sheets: list[dict[str, Any]] = []
        for ws in wb.worksheets:
            cells: list[dict[str, Any]] = []
            header_row_index: int | None = None
            for row_idx, row in enumerate(
                ws.iter_rows(max_row=min(ws.max_row or 200, 200)), start=1
            ):
                for cell in row:
                    if cell.value is None:
                        continue
                    text = str(cell.value).strip()
                    if not text:
                        continue
                    col_letter = get_column_letter(cell.column)
                    position = f"{col_letter}{cell.row}"
                    cells.append(
                        {
                            "position": position,
                            "value": text,
                            "row": cell.row,
                            "col": col_letter,
                        }
                    )
                    cell_map[position] = text
                    _maybe_record_field_position(text, position, field_positions)
                # Heuristic: first row with ≥ 3 short text cells = headers
                if header_row_index is None:
                    short_text_cells = [
                        c
                        for c in row
                        if c.value and isinstance(c.value, str) and len(c.value.strip()) < 40
                    ]
                    if len(short_text_cells) >= 3:
                        header_row_index = row_idx
            sheets.append(
                {
                    "name": ws.title,
                    "header_row": header_row_index,
                    "cells": cells[:200],  # cap returned size
                }
            )
            if header_row_index and not product_table_config.get("start_row"):
                product_table_config = {
                    "start_row": header_row_index + 1,
                    "header_row": header_row_index,
                }
    finally:
        wb.close()

    return {
        "field_positions": field_positions,
        "product_table_config": product_table_config,
        "cell_map": cell_map,
        "template_styles": None,
        "notes": "Phase 4 deterministic Excel analysis — Phase 5 will add Gemini",
        "file_url": "",  # caller fills this in
        "template_html": None,
        "field_mapping_preview": [],
        "sheets": sheets,
    }


_FIELD_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bPO\s*(?:no|number)?\b|発注番号|注文番号", re.IGNORECASE), "po_number"),
    (re.compile(r"\bship\s*name\b|船名|vessel", re.IGNORECASE), "ship_name"),
    (re.compile(r"\bvendor\b|\bsupplier\b|供应商", re.IGNORECASE), "vendor_name"),
    (re.compile(r"\bdelivery\s*date\b|納品|交货日期", re.IGNORECASE), "delivery_date"),
    (re.compile(r"\border\s*date\b|发注日|注文日", re.IGNORECASE), "order_date"),
    (re.compile(r"\bcurrency\b|币种|通貨", re.IGNORECASE), "currency"),
    (re.compile(r"\bport\b|港口|ポート", re.IGNORECASE), "destination_port"),
    (re.compile(r"\btotal\s*amount\b|总金额|合計金額", re.IGNORECASE), "total_amount"),
]


def _maybe_record_field_position(
    text: str, position: str, field_positions: dict[str, dict[str, Any]]
) -> None:
    for pattern, key in _FIELD_HINTS:
        if pattern.search(text) and key not in field_positions:
            field_positions[key] = {
                "position": position,
                "label": text,
                "data_type": "string",
            }
            return
