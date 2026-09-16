"""Section 6 — Masterdata: contract_price editing path (2026-06-22).

为什么这套测试存在：
    `contract_price` 字段从 R2 上线起就 wired up（model / schema /
    matching / financials），但前端的产品编辑对话框**漏了输入框**
    —— Felix 2026-06-22 反馈："合同价没地方 update"。
    根因不在后端，是前端 form schema 缺字段、PATCH payload 永远
    不带 contract_price。

    本期补全前端 input + 把 UI label 从「合同价」改为「卖价」（同字段，
    新名字），同时把 contract_price 加进官方上传模板。

    这套测试锁住三条契约：
    1. PATCH /api/data/products/{id} 接受 contract_price 并真正持久化
    2. 上传模板里包含 contract_price 列（之前 missing — Explore 报告的 Gap）
    3. 上传 service 接受新的中文别名 "卖价"

设计方法：
    - test 1: HTTP 端到端，证明前端发的 payload 能到 DB
    - test 2: 模板字节流解出 openpyxl workbook，扫描 header
    - test 3: alias 表纯函数断言
"""

from __future__ import annotations

import io
from decimal import Decimal

from openpyxl import load_workbook

from domains.masterdata.models import Product
from test_v2.fixtures.helpers import login, seed_user


# ─── 1. PATCH /products/{id} 真的持久化 contract_price ────────


def test_patch_product_persists_contract_price(client, db):
    """前端编辑对话框的核心契约：PATCH 带 contract_price → 入库 → GET 回读到。

    Locks the 2026-06-22 fix: prior to this, the frontend form schema
    omitted contract_price entirely, so even though the backend accepted
    the field, no UI could ever send it. A regression that re-drops the
    field from PATCH semantics surfaces here as a value mismatch.
    """
    seed_user(db, email="ed@example.com", role="admin")
    headers = login(client, "ed@example.com")

    # Seed a product without contract_price.
    from test_v2.fixtures.helpers import seed_product

    p = seed_product(
        db, code="EDT-001", name="Test Beef", price=850.00
    )
    assert p.contract_price is None, "fixture should start clean"

    # PATCH it.
    r = client.patch(
        f"/api/data/products/{p.id}",
        json={"contract_price": 1050.50, "expected_revision": p.revision},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["contract_price"] == 1050.50, (
        "PATCH must echo contract_price in response so the dialog can"
        f" refresh state without a second fetch; got {body!r}"
    )

    # Re-fetch via list (different code path) and confirm DB-side.
    db.refresh(p)
    assert p.contract_price == Decimal("1050.50")


def test_patch_product_can_clear_contract_price(client, db):
    """卖价是 nullable —— 编辑时清空必须真的清空，不能默默忽略。

    The dialog sends `contract_price: null` when the user wipes the
    input. Backend must persist NULL, not silently keep the old value
    (which would be the 2026-06-08 pre-fix bug if `Decimal('0')` falsy
    handling ever regresses).
    """
    from test_v2.fixtures.helpers import seed_product

    seed_user(db, email="ed2@example.com", role="admin")
    headers = login(client, "ed2@example.com")

    p = seed_product(db, code="EDT-002", name="Test Pork", price=500)
    p.contract_price = Decimal("999.99")
    db.commit()
    db.refresh(p)
    assert p.contract_price == Decimal("999.99")

    r = client.patch(
        f"/api/data/products/{p.id}",
        json={"contract_price": None, "expected_revision": p.revision},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["contract_price"] is None

    db.refresh(p)
    assert p.contract_price is None, (
        f"clearing contract_price must persist as NULL; got {p.contract_price!r}"
    )


# ─── 2. 上传模板里包含 contract_price 列 ────────────────────


def test_upload_template_contains_contract_price_column():
    """官方下载的 Excel 模板必须有 contract_price 列；2026-06-22 修复之前
    该列**不在 OPTIONAL_COLUMNS 里**，用户下载模板根本看不到这个字段。

    Reads the actual workbook bytes the user would download — guards
    against header drift between template and upload pipeline.
    """
    from scripts.generate_product_upload_template import (
        ALL_COLUMNS,
        build_template,
    )

    # Canonical name MUST be in column catalog (covers _HEADER_ALIASES wiring).
    canonical_names = {col[0] for col in ALL_COLUMNS}
    assert "contract_price" in canonical_names, (
        "generate_product_upload_template.py is missing contract_price"
        " from ALL_COLUMNS — users can't see it in the template"
    )

    # The rendered workbook must carry the column AND label it as a
    # selling price (the user-facing UI label "卖价"), not "合同价"
    # which is the legacy name we're moving away from.
    wb = build_template()
    # The data sheet's header row carries the **canonical** names
    # (contract_price). The user-facing display label (「卖价」) lives in
    # the cell comment / tooltip — that's where we check the rename
    # actually surfaced, since the canonical name didn't change.
    ws = wb["产品数据"] if "产品数据" in wb.sheetnames else wb.active
    header_row = [c.value for c in ws[1]]
    assert "contract_price" in header_row, (
        f"template data sheet missing contract_price column; row = {header_row}"
    )
    # Find the column and verify its tooltip uses 「卖价」 not 「合同价」.
    col_idx = header_row.index("contract_price") + 1
    comment = ws.cell(row=1, column=col_idx).comment
    assert comment is not None, "contract_price header has no tooltip — UX regression"
    assert "卖价" in comment.text, (
        f"tooltip should display 卖价 (new label); got: {comment.text!r}"
    )


# ─── 3. 上传 service 接受新别名 "卖价" ─────────────────────


def test_upload_header_alias_accepts_sellprice():
    """The 2026-06-22 rename adds 「卖价」 as the canonical user-facing
    label. Users who hand-edit a template (or use an old internal
    spreadsheet they re-headered) must have their column recognized.

    Old aliases (合同价 / 合同卖价 / 合約価格 / 契約価格) MUST remain
    accepted — older spreadsheets still circulate in operations and
    breaking them retroactively would be a silent data loss.
    """
    from domains.masterdata.upload.service import _HEADER_ALIASES

    aliases = _HEADER_ALIASES["contract_price"]
    assert "卖价" in aliases, "new canonical UI label missing from aliases"
    # Backward-compat: every old alias still works.
    for legacy in ("合同价", "合同卖价", "合約価格", "契約価格"):
        assert legacy in aliases, (
            f"backward-compat alias {legacy!r} was dropped — old"
            " spreadsheets will silently lose contract_price"
        )
