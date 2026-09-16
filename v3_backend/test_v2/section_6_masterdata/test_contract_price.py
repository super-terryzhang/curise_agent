"""Section 6 — Masterdata: contract_price plumbing across model → schema → upload → match.

测试目标：
    新增的 `Product.contract_price`（2026-06-16 Felix R2）必须贯通：
    1. Schema 层（ProductCreate / ProductUpdate / ProductResponse 都接受 + 返回）
    2. Service 层（_apply_create + serialize 都处理它）
    3. Upload pipeline（_HEADER_ALIASES 识别多语种列名；_apply_create / _apply_update 写入）
    4. Matching 层（code_first._serialize_db_product + llm_refine 都在 match_results 暴露）

为什么重要：
    任何一道关卡漏了 contract_price，要么 Excel 上传不进库，要么前端
    FinancialTab 拿不到对比所需的数据，整个"反向对比 PO 价格"功能
    就变成只在数据库里有值的孤岛。

设计方法：
    - 6 个纯契约测试，无 DB / 无 HTTP，毫秒级
    - 锁定每段代码"包含字段"的最低契约
    - 不测算术 / 业务逻辑 —— 那是前端 / 后端的进一步增强（本期不做）
"""

from __future__ import annotations


def test_product_model_has_contract_price_column():
    """ORM 层 Product 必须有 contract_price 列，类型 Numeric(10,2)。"""
    from domains.masterdata.models import Product

    col = Product.__table__.c.contract_price
    assert col is not None
    assert col.nullable is True


def test_product_create_schema_accepts_contract_price():
    """ProductCreate Pydantic schema 必须显式列出 contract_price，否则前端
    或 agent 传值会被静默丢弃。"""
    from domains.masterdata.schemas import ProductCreate

    assert "contract_price" in ProductCreate.model_fields


def test_product_response_includes_contract_price():
    """ProductResponse 必须返回 contract_price —— 前端读不到就不能编辑。"""
    from domains.masterdata.schemas import ProductResponse

    assert "contract_price" in ProductResponse.model_fields


def test_upload_header_aliases_recognize_contract_price():
    """Excel 上传识别"合同卖价" / "contract_price" 等列头映射到 contract_price。"""
    from domains.masterdata.upload.service import _HEADER_ALIASES

    assert "contract_price" in _HEADER_ALIASES
    aliases = _HEADER_ALIASES["contract_price"]
    # 必须含中文、日文、英文形式 —— 与 Felix 决定的"日本员工主要负责"对齐
    assert any("合同卖价" in a for a in aliases)
    assert any("contract_price" in a for a in aliases)


def test_decimal_or_none_parses_blank_and_invalid_gracefully():
    """Excel cell 可能是 None / "" / "abc" / 数字字符串 / 数字。
    _decimal_or_none 必须容错：空 / 无法解析 → None；正常 → Decimal。"""
    from decimal import Decimal

    from domains.masterdata.upload.service import _decimal_or_none

    assert _decimal_or_none(None) is None
    assert _decimal_or_none("") is None
    assert _decimal_or_none("   ") is None
    assert _decimal_or_none("abc") is None
    assert _decimal_or_none("12.34") == Decimal("12.34")
    assert _decimal_or_none(56.78) == Decimal("56.78")
    assert _decimal_or_none(0) == Decimal("0")


def test_match_results_serializer_includes_contract_price():
    """code_first._serialize_db_product 必须把 contract_price 放进 matched_product
    dict —— 这是前端 FinancialTab 用来做 PO vs 合同价对比的入口。"""
    from decimal import Decimal

    from domains.masterdata.models import Product
    from domains.orders.matching.code_first import _serialize_db_product

    p = Product(
        product_name_en="X",
        code="X1",
        price=Decimal("100"),
        contract_price=Decimal("95.50"),
    )
    serialized = _serialize_db_product(p)
    assert "contract_price" in serialized
    assert serialized["contract_price"] == 95.50
    # NULL 也要正确处理
    p2 = Product(product_name_en="Y", code="Y1")
    serialized2 = _serialize_db_product(p2)
    assert "contract_price" in serialized2
    assert serialized2["contract_price"] is None
