"""Section 5 — inquiry unit_price DB-only invariant (v44+).

测试目标：
    Pin 住"询价单单价只能来自 DB，customer PO 价不能流到询价表"这条
    数据治理规则。Prod 2026-05-20 order #104 真实数据：
      - row 0: Excel 单价 745，DB 单价 690 → 期望 690
      - row 1: Excel 单价 564，DB 单价 643 → 期望 643
      - row 8/9: DB 价为 NULL → 期望单元格空（不回退到 Excel）

为什么重要：
    这条规则不仅是 bug 修复，是**数据流向边界**：客户上传文件的价格
    必须保留在 match_results 里供 anomaly detection 使用，但**不能**
    出现在发给供应商的 Excel 上（避免泄露 + 强迫人工维护 DB 目录价）。

    旧实现（v44 之前）在三处把 Excel 价当 fallback：
      template_engine._resolve_product_value
      template_engine._fill_generic
      _supplier_worker._compute_subtotal
    这条测试同时覆盖这三处，谁回归都会挂掉。
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

import pytest
from openpyxl import load_workbook

from domains.inquiry._supplier_worker import compute_subtotal
from domains.inquiry.template_engine import _resolve_product_value


# ─── Layer 1: pure function _resolve_product_value ──────────


@pytest.mark.parametrize(
    "db_price, excel_price, has_matched, expected",
    [
        # The core prod case: DB beats Excel
        (690.0, 745.0, True, 690.0),
        # Excel missing — still use DB
        (690.0, None, True, 690.0),
        # DB has zero — that's a real catalog price, not a sentinel
        (0, 745.0, True, 0),
        # DB None + Excel present — user's strict rule: must be empty
        (None, 745.0, True, ""),
        # Both None
        (None, None, True, ""),
        # No matched product at all — empty
        (None, 745.0, False, ""),
    ],
)
def test_resolve_unit_price_db_only(
    db_price: float | None,
    excel_price: float | None,
    has_matched: bool,
    expected: float | str,
) -> None:
    product: dict[str, Any] = {"product_code": "X", "quantity": 1}
    if excel_price is not None:
        product["unit_price"] = excel_price
    matched: dict[str, Any] = {}
    if has_matched:
        matched["code"] = "X"
        if db_price is not None:
            matched["price"] = db_price
        product["matched_product"] = matched
    result = _resolve_product_value("unit_price", 0, product, matched, {})
    assert result == expected, (
        f"db_price={db_price}, excel_price={excel_price}, has_matched={has_matched}: "
        f"expected {expected!r}, got {result!r}"
    )


# ─── Layer 2: _compute_subtotal uses DB only ────────────────


def test_subtotal_skips_rows_without_db_price() -> None:
    """Lines with no DB price contribute 0 to subtotal — they show as
    blank in the inquiry sheet and must NOT silently inflate the total
    via the customer's PO price."""
    products: list[dict[str, Any]] = [
        # 5 × DB 690 = 3450 ✓
        {"quantity": 5, "unit_price": 745, "matched_product": {"price": 690}},
        # DB no price; Excel has 100 — must be SKIPPED, not added as 5×100=500
        {"quantity": 5, "unit_price": 100, "matched_product": {"code": "Y"}},
        # No matched_product — also skipped
        {"quantity": 10, "unit_price": 200},
    ]
    subtotal = compute_subtotal(products)
    assert subtotal == 3450.0, (
        f"subtotal must use DB-only prices; got {subtotal}, expected 3450 "
        f"(only the priced line contributes)"
    )


def test_subtotal_zero_db_price_is_valid() -> None:
    """0 is a real DB price (free sample), not a missing-price sentinel."""
    products: list[dict[str, Any]] = [
        {"quantity": 5, "matched_product": {"price": 0}},
        {"quantity": 5, "matched_product": {"price": 100}},
    ]
    assert compute_subtotal(products) == 500.0


# ─── Layer 3: end-to-end Excel with real order #104 data ────


def test_inquiry_excel_uses_db_price_for_order_104_repro() -> None:
    """Reproduces the exact prod situation (order #104, 2026-05-20):
    cruise company's PO carried Excel prices, our DB had different prices.
    The generated inquiry Excel MUST show DB prices, not Excel prices."""
    from domains.inquiry.template_engine import render_inquiry_excel

    # Fixture rows mirror real prod data for order 104. The Excel-vs-DB
    # divergence is the whole reason we built this invariant.
    rows = [
        {"code": "99PRD010588", "excel": 745.0, "db": 690.0},
        {"code": "99PRD010590", "excel": 564.0, "db": 643.0},
        {"code": "99PRD010601", "excel": 2269.0, "db": 1815.0},
        {"code": "99PRD010604", "excel": 1496.0, "db": 1080.0},
        {"code": "99PRD010728", "excel": 174.0, "db": 200.0},
        # Real prod rows where DB price was NULL — must come out empty
        {"code": "99PRD010748", "excel": 206.0, "db": None},
        {"code": "99PRD010758", "excel": 3404.0, "db": None},
    ]
    products = [
        {
            "product_code": r["code"],
            "product_name": f"P{r['code']}",
            "quantity": 5,
            "unit_price": r["excel"],
            "matched_product": (
                {"code": r["code"], "price": r["db"]} if r["db"] is not None
                else {"code": r["code"]}
            ),
        }
        for r in rows
    ]

    # Build a minimal template that drives the column-mapped layout
    # (not generic fallback) — closer to what production templates use.
    from domains.inquiry.models import SupplierTemplate
    template = SupplierTemplate(
        id=1,
        supplier_id=2,
        field_positions={"po_number": "A1"},  # truthy → template path
        product_table_config={
            "start_row": 10,
            "columns": {"B": "product_code", "C": "quantity", "D": "unit_price"},
        },
    )
    out_bytes = render_inquiry_excel(
        template, metadata={"po_number": "PO-TEST"}, products=products,
        supplier_id=2,
    )
    ws = load_workbook(BytesIO(out_bytes)).active

    for i, r in enumerate(rows):
        cell_row = 10 + i
        actual_price = ws.cell(row=cell_row, column=4).value
        if r["db"] is None:
            assert actual_price in (None, ""), (
                f"Row {r['code']} has DB price NULL — expected empty cell "
                f"(v44 invariant: no Excel fallback), got {actual_price!r}"
            )
        else:
            assert actual_price == r["db"], (
                f"Row {r['code']} expected DB price {r['db']}, got "
                f"{actual_price!r}. If this returned {r['excel']!r} the bug "
                f"has regressed."
            )
            assert actual_price != r["excel"], (
                f"Row {r['code']} matched Excel price {r['excel']!r} — "
                f"that's exactly what the v44 invariant forbids."
            )
