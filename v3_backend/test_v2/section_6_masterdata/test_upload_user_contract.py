"""Section 6 — Masterdata upload, USER CONTRACT tests.

这个文件不测内部实现（那是 stage1-5 的事），而是测**用户契约**：
    一份按模板填的 Excel 上传，系统在每一种"用户最可能犯的错"或
    "用户最可能正确填的场景"下都给出**预期、可恢复的结果**。

设计参考（测试工程师工作流）：
    1. 等价类划分 — 每个字段按 "valid / invalid / boundary / empty" 列出
    2. 错误猜测 — 真实用户在 Excel 里最容易犯的操作（数字 cell 当 code、
       同 batch 重复 SKU、把示例行留着等）
    3. 行级隔离 — 一行错不应阻断其他行（per-row savepoint 已存在但需要场
       景测试）
    4. 不变量 — 已 match 的产品的身份字段（name / code）不应被批量改写

每个测试一个明确的"用户故事"：人能读懂 docstring 第一段就知道**这条
测试在保护什么用户体验**。
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from openpyxl import Workbook

from domains.masterdata.models import Category, Country, Port, Product, Supplier
from domains.masterdata.upload import (
    commit_batch,
    parse_excel,
    resolve_and_score,
)
from domains.masterdata.upload.models import StagingProduct
from test_v2.fixtures.helpers import make_excel, seed_product

# ─── C1: product_name 必填行为 ───────────────────────────


def test_product_name_with_only_whitespace_is_rejected(db):
    """用户故事：用户在 product_name 单元格里只敲了几个空格就保存了 —
    系统不能把它当成有效产品名（会变成无名产品）。期待行级 error，
    其他正常行不受影响。"""
    blob = make_excel(
        [
            {"product_name": "Real Product", "price": 10},
            {"product_name": "   ", "price": 20},  # 只有空格
            {"product_name": "Another Real", "price": 30},
        ]
    )
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=1)
    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)

    # parse 期间 `_str_or_none` 已经把纯空格 trim 成 None；resolve 把它
    # 标成 error。其他两行保持 new/exact 状态。
    assert resolved.error_rows == 1
    assert resolved.new_rows + resolved.matched_exact + resolved.matched_fuzzy == 2

    bad = (
        db.query(StagingProduct)
        .filter(StagingProduct.batch_id == batch.id, StagingProduct.row_index == 2)
        .one()
    )
    assert bad.match_status == "error"
    assert "missing product_name" in (bad.validation_errors or [])


# ─── C2: product_code 数字 cell + 重复 ──────────────────


def test_product_code_typed_as_number_in_excel_becomes_string(db):
    """用户故事：用户在 Excel 里把 product_code 列没设文本格式，直接
    敲了数字。系统不能把它跟字符串型 code 区别对待，要么都按字符串
    存，要么都按数字存。"""
    wb = Workbook()
    ws = wb.active
    ws.append(["product_name", "product_code"])
    ws.append(["Numeric Code Item", 12345])  # int cell — not string
    buf = io.BytesIO()
    wb.save(buf)

    batch = parse_excel(db, file_bytes=buf.getvalue(), filename="t.xlsx", user_id=1)
    sp = db.query(StagingProduct).filter(StagingProduct.batch_id == batch.id).one()
    # 系统总是按字符串存 code（这样跨上传比较时不会因为数字 vs 字符串
    # 类型差异导致 "12345" != 12345 错过匹配）。
    assert sp.product_code == "12345"
    assert isinstance(sp.product_code, str)


def test_duplicate_product_code_within_same_batch_both_create(db):
    """用户故事：用户复制粘贴时不小心两行 product_code 相同。当前
    系统行为是两行都创建（DB 没在 Product.code 上加 unique constraint）。

    这个测试**文档化当前行为**。这是已知的非理想行为（重复 SKU 应该
    被 dedupe），但今天的契约就是这样；改之前先 pin 住，免得有人偷偷
    改了 dedupe 行为造成生产意外。
    """
    blob = make_excel(
        [
            {"product_name": "Dup A", "product_code": "DUP-1"},
            {"product_name": "Dup B", "product_code": "DUP-1"},  # same code
        ]
    )
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=1)
    resolve_and_score(db, batch_id=batch.id, user_id=1)
    result = commit_batch(db, batch_id=batch.id, user_id=1)
    # Both rows fire _apply_create (resolve doesn't see the in-batch
    # duplicate — it only compares against DB). So we end up with two
    # Product rows that share `code`.
    assert result["created"] == 2

    products = db.query(Product).filter(Product.code == "DUP-1").all()
    assert len(products) == 2  # confirmed: NO dedupe today


# ─── C4: FK 多行混合 (row isolation) ────────────────────


def test_fk_miss_on_one_row_does_not_block_others(db):
    """用户故事：用户上传 50 行产品，其中 1 行的 supplier 名字打错了。
    打错的那一行被 reject，其他 49 行该 create 的 create、该 update 的
    update。**绝对不能因为一行写错而整批拒收**——那种全量重传成本太高。"""
    db.add(Supplier(name="Real Supplier"))
    db.commit()

    blob = make_excel(
        [
            {"product_name": "Row 1 OK", "supplier": "Real Supplier"},
            {"product_name": "Row 2 BAD", "supplier": "Nonexistent Co"},  # typo
            {"product_name": "Row 3 OK", "supplier": "Real Supplier"},
        ]
    )
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=1)
    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)

    # 2 OK + 1 error
    assert resolved.new_rows == 2
    assert resolved.error_rows == 1

    result = commit_batch(db, batch_id=batch.id, user_id=1)
    assert result["created"] == 2
    assert result["errors"] == 1
    # 两条 OK 真的写到 DB 了
    assert db.query(Product).filter(Product.product_name_en == "Row 1 OK").count() == 1
    assert db.query(Product).filter(Product.product_name_en == "Row 3 OK").count() == 1
    # 错误那条没写
    assert db.query(Product).filter(Product.product_name_en == "Row 2 BAD").count() == 0


# ─── C5: 日期多格式 end-to-end ──────────────────────────


@pytest.mark.parametrize(
    "raw_date,expected_iso",
    [
        ("2026-05-30", "2026-05-30"),
        ("2026/05/30", "2026-05-30"),
        ("2026.05.30", "2026-05-30"),
        ("2026-05-30 12:34:56", "2026-05-30"),
        ("2026-05-30T00:00:00", "2026-05-30"),
    ],
)
def test_effective_date_accepts_multiple_formats_end_to_end(db, raw_date, expected_iso):
    """用户故事：日本来源的 Excel 写 2026/05/30，国际来源写 2026-05-30，
    Excel 导出的有时是带时间的 ISO datetime。三种都要接，结果一样。"""
    blob = make_excel(
        [{"product_name": f"Date Test {raw_date}", "effective_from": raw_date}]
    )
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=1)
    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)
    assert resolved.error_rows == 0
    commit_batch(db, batch_id=batch.id, user_id=1)

    p = (
        db.query(Product)
        .filter(Product.product_name_en == f"Date Test {raw_date}")
        .one()
    )
    assert p.effective_from is not None
    assert p.effective_from.strftime("%Y-%m-%d") == expected_iso


# ─── C6: update 行为 ────────────────────────────────────


def test_update_does_not_change_product_name_or_code(db):
    """用户故事：用户已有产品 SKU "BEEF-001" 名叫 "Beef Tenderloin"。
    上传时第 1 列里手贱写成了 "Beef" 想看会发生什么。**系统必须保留
    原名 + 原 code**——name / code 是产品身份，批量改身份会让历史
    订单引用错乱。其他字段（price/unit/...）正常更新。"""
    p = seed_product(db, code="BEEF-001", name="Beef Tenderloin", price=4000, unit="KG")
    original_name = p.product_name_en
    original_code = p.code

    blob = make_excel(
        [
            {
                "product_name": "Beef (renamed!)",  # 用户改名
                "product_code": "BEEF-001",
                "price": 4500,
                "unit": "PCS",
            }
        ]
    )
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=1)
    resolve_and_score(db, batch_id=batch.id, user_id=1)
    result = commit_batch(db, batch_id=batch.id, user_id=1)
    assert result["updated"] == 1
    assert result["created"] == 0  # match via code → update

    db.refresh(p)
    # 身份字段保留
    assert p.product_name_en == original_name
    assert p.code == original_code
    # 其他字段更新
    assert float(p.price) == 4500.0
    assert p.unit == "PCS"


def test_update_writes_all_extended_fields_for_existing_product(db):
    """用户故事：DB 里已有一个基本产品（只有 name + code + price）。
    用户用模板上传，把 brand / supplier / currency / 各种字段都填了。
    这些扩展字段必须全部生效——单条编辑能改，批量也得能改。"""
    sup = Supplier(name="Best Co")
    cat = Category(name="MEAT")
    ctry = Country(name="USA")
    port = Port(name="LAX")
    db.add_all([sup, cat, ctry, port])
    db.commit()
    db.refresh(sup)
    db.refresh(cat)
    db.refresh(ctry)
    db.refresh(port)

    # Seed at (USA, LAX) — must match the Excel's (country, port) for
    # Rule 1 to fire and produce an update under the strict identity
    # contract. Without this the seed would land at the default
    # (TestCountry, TestPort) and the Excel would resolve as "new".
    p = seed_product(
        db,
        code="EXT-1",
        name="Basic Item",
        price=10,
        unit="EA",
        country_id=ctry.id,
        port_id=port.id,
    )
    blob = make_excel(
        [
            {
                "product_name": "Basic Item",  # match by code, name stays
                "product_code": "EXT-1",
                "product_name_jp": "日本語名",
                "brand": "BrandX",
                "category": "MEAT",
                "supplier": "Best Co",
                "country": "USA",
                "port": "LAX",
                "currency": "USD",
                "unit_size": "5KG",
                "pack_size": "10x5KG",
                "country_of_origin": "Texas, USA",
                "effective_from": "2026-01-01",
                "effective_to": "2026-12-31",
            }
        ]
    )
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=1)
    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)
    assert resolved.error_rows == 0

    result = commit_batch(db, batch_id=batch.id, user_id=1)
    assert result["updated"] == 1

    db.refresh(p)
    assert p.product_name_jp == "日本語名"
    assert p.brand == "BrandX"
    assert p.category_id == cat.id
    assert p.supplier_id == sup.id
    assert p.country_id == ctry.id
    assert p.port_id == port.id
    assert p.currency == "USD"
    assert p.unit_size == "5KG"
    assert p.pack_size == "10x5KG"
    assert p.country_of_origin == "Texas, USA"
    assert p.effective_from is not None
    assert p.effective_to is not None


def test_update_empty_cells_do_not_overwrite_existing_values(db):
    """用户故事：用户下载现有产品的导出（手动复刻），改了价格，但其他
    列没动（留空，或者删除整列）。期望：只有改的字段被更新，其他字段
    **保留原值**——不能因为单元格空就把数据库现有的 brand 抹成 None。"""
    p = seed_product(db, code="KEEP-1", name="Has Many Fields", price=100, unit="KG")
    # 手动把扩展字段填上初始值（模拟历史 DB 状态）
    p.brand = "Existing Brand"
    p.currency = "USD"
    p.unit_size = "10KG"
    db.commit()

    # 用户上传只改 price，其他列**空着**
    blob = make_excel(
        [{"product_name": "Has Many Fields", "product_code": "KEEP-1", "price": 200}]
    )
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=1)
    resolve_and_score(db, batch_id=batch.id, user_id=1)
    commit_batch(db, batch_id=batch.id, user_id=1)

    db.refresh(p)
    # price 变了
    assert float(p.price) == 200.0
    # 其他原有字段被保留
    assert p.brand == "Existing Brand"
    assert p.currency == "USD"
    assert p.unit_size == "10KG"


# ─── C8: 用户把示例行没删 ─────────────────────────────


def test_example_rows_left_in_template_resolve_against_real_masterdata(db):
    """用户故事：用户下载模板，**没删示例 3 行**，直接在下面接着加自己
    的 5 行。10 行（其实是 8 行真数据，因为最后一行只有 product_name 是
    完整的）一起上传。

    如果用户的环境**已经有** USA/Japan/FRUIT/DAIRY/Sunkist/Morinaga 这
    些 masterdata（这是真实场景，因为这些是常见值），示例行也会被处理
    成新产品。这不是 bug ——是模板设计的合理后果。我们要验证：
      1. 示例行不会因为找不到 FK 就把 batch 整个搞挂
      2. 用户自己的行该 create / update 还是 create / update

    模板示例的 3 行 + 我们加 2 行测试数据 = 5 行 new。"""
    # 模拟用户环境已有完整 masterdata
    for n in ("USA", "Japan"):
        db.add(Country(name=n))
    for n in ("FRUIT", "DAIRY"):
        db.add(Category(name=n))
    for n in ("Sunkist Growers Inc.", "Morinaga Milk Industry Co., Ltd."):
        db.add(Supplier(name=n))
    db.add(Port(name="Yokohama"))
    db.commit()

    # 直接复用真实模板 + 在它末尾追加 2 行用户数据
    template_path = (
        Path(__file__).resolve().parents[2]
        / "static"
        / "templates"
        / "product_upload_template.xlsx"
    )
    from openpyxl import load_workbook

    wb = load_workbook(template_path)
    ws = wb["产品数据"]
    # 找到列号 — 注意 country / port 是必填列（strict-contract 2026-05-27）
    headers = [ws.cell(row=1, column=c).value for c in range(1, 17)]
    name_col = headers.index("product_name") + 1
    code_col = headers.index("product_code") + 1
    price_col = headers.index("price") + 1
    country_col = headers.index("country") + 1
    port_col = headers.index("port") + 1

    # 模板的示例 3 行在 row 2-4；用户数据从 row 5 起。每行都必须填
    # country + port —— 严格身份契约的必填项。
    ws.cell(row=5, column=name_col, value="User Item A")
    ws.cell(row=5, column=code_col, value="USR-A")
    ws.cell(row=5, column=price_col, value=11.11)
    ws.cell(row=5, column=country_col, value="USA")
    ws.cell(row=5, column=port_col, value="Yokohama")
    ws.cell(row=6, column=name_col, value="User Item B")
    ws.cell(row=6, column=code_col, value="USR-B")
    ws.cell(row=6, column=price_col, value=22.22)
    ws.cell(row=6, column=country_col, value="Japan")
    ws.cell(row=6, column=port_col, value="Yokohama")

    buf = io.BytesIO()
    wb.save(buf)

    batch = parse_excel(db, file_bytes=buf.getvalue(), filename="mixed.xlsx", user_id=1)
    assert batch.total_rows == 5  # 3 example + 2 user
    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)
    assert resolved.error_rows == 0  # all FKs resolve
    assert resolved.new_rows == 5
    result = commit_batch(db, batch_id=batch.id, user_id=1)
    assert result["created"] == 5
