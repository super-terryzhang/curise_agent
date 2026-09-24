"""Section 4 — Orders: 产品匹配流水线 (`domains/orders/matching/`).

测试目标:
    匹配流水线分三段, 每段都要单元测试:
      1. `geo.resolve_geo(order, db)` — 把 Order 上的 destination_port / currency /
         metadata 解析成 (country_id, port_id, delivery_date)。
         · destination_port 命中 port name → 该 port + 它的 country 胜出
         · 没 port 命中时 currency 解 country
         · port_code (在 metadata.extra_fields 里) 是次优先级
      2. `code_first.match_by_code(inputs, pool)` — 产品代码完全匹配 (大小写无关)。
         · 命中 → match_status="matched", match_score=1.0
         · 没 code 或 code 不在 pool → 进 unmatched
         · 输入顺序必须保留
      3. `llm_refine.apply_refinement(all_results, unmatched, pool)` —
         FakeMatcher 基于 token 重叠率, 只在 jaccard ≥ 0.34 且重叠 ≥ 2 token 时
         升级。**关键不变量**: 它只能把 not_matched 升级成 matched, 永远不能
         把已经 matched 的降级。
      4. 集成 `service.run_matching(order, db)` — 三段串起来后产出
         match_results + match_statistics, 且把 country/port/delivery_date 写回
         Order 列。

为什么重要:
    匹配错了 → 询价单发到错的供应商, 价格、库存、税务全部连锁错。
    LLM refine 的单向语义 (只升不降) 是 v2 时代的一个 bug 修复点 — 早期版本
    LLM 偶尔会把高置信度的 code 命中"重新评估"成 not_matched, 这条规则
    必须钉死。

设计方法:
    - geo: 用真 SQLite + masterdata models 直接插 Country/Port 行, 喂给
      resolve_geo, 断言返回的 id。
    - code_first: pure function, 用 fake Product 对象 (用 SimpleNamespace
      模拟必要属性即可) 喂进去, 断言匹配字典。
    - llm_refine: 直接调 FakeMatcher.refine, 不走 registry; 然后单独测
      apply_refinement 的"不降级"语义。
    - service.run_matching: 端到端用真 db + seed_product 验证整段流水线。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from domains.masterdata.models import Country, Port
from domains.orders.matching import code_first, geo, llm_refine, service
from domains.orders.matching.llm_refine import FakeMatcher
from domains.orders.models import Order
from test_v2.fixtures.helpers import seed_product, seed_user

# ─── Helpers for building master geo rows ─────────────────────


def _add_country(db, *, name: str, code: str) -> Country:
    c = Country(name=name, code=code, status=True)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _add_port(db, *, name: str, country_id: int, code: str | None = None) -> Port:
    p = Port(name=name, code=code, country_id=country_id, status=True)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _make_order(
    *,
    destination_port: str | None = None,
    currency: str | None = None,
    delivery_date: str | None = None,
    metadata: dict[str, Any] | None = None,
    po_number: str | None = None,
    ship_name: str | None = None,
    products: list[dict[str, Any]] | None = None,
) -> Order:
    """Construct an Order in memory (no DB insert needed for geo unit tests)."""
    return Order(
        user_id=1,
        document_id=None,
        filename="x.pdf",
        file_type="pdf",
        status="extracted",
        destination_port=destination_port,
        currency=currency,
        delivery_date=delivery_date,
        order_metadata=metadata or {},
        po_number=po_number,
        ship_name=ship_name,
        products=products or [],
    )


# ─── Geo resolver: destination_port wins ──────────────────────


def test_destination_port_matches_by_name_substring(db):
    """`destination_port="TOKYO"` 在 port name "Port of Tokyo" 里, 应当命中。"""
    jp = _add_country(db, name="Japan", code="JPN")
    tokyo = _add_port(db, name="Port of Tokyo", country_id=jp.id)
    _add_port(db, name="Singapore", country_id=jp.id)  # 干扰项

    order = _make_order(destination_port="Tokyo")
    result = geo.resolve_geo(order, db)

    assert result["port_id"] == tokyo.id
    assert result["country_id"] == jp.id  # port → country 反查


def test_destination_port_wins_over_currency(db):
    """当 destination_port 已经能解析出 port (因而拿到 country) 时, currency
    的 country 推断必须让位 — port 是更强的信号。"""
    jp = _add_country(db, name="Japan", code="JPN")
    us = _add_country(db, name="United States", code="USA")
    tokyo = _add_port(db, name="Tokyo", country_id=jp.id)

    # currency 是 USD 但 destination_port 是 Tokyo — port 胜
    order = _make_order(destination_port="Tokyo", currency="USD")
    result = geo.resolve_geo(order, db)

    assert result["port_id"] == tokyo.id
    assert result["country_id"] == jp.id
    assert result["country_id"] != us.id


def test_port_code_in_extras_resolves_port(db):
    """metadata.extra_fields.port_code 是 port code 时, 也应该被匹中。"""
    jp = _add_country(db, name="Japan", code="JPN")
    yokohama = _add_port(db, name="Yokohama", country_id=jp.id, code="YOK")

    order = _make_order(metadata={"extra_fields": {"port_code": "YOK"}})
    result = geo.resolve_geo(order, db)

    assert result["port_id"] == yokohama.id


# ─── Geo resolver: currency → country ─────────────────────────


@pytest.mark.parametrize(
    "currency,country_code",
    [
        ("JPY", "JPN"),
        ("USD", "USA"),
        ("AUD", "AUS"),
        ("THB", "THA"),
        ("CNY", "CHN"),
        ("GBP", "GBR"),
        ("SGD", "SGP"),
    ],
)
def test_currency_resolves_country(db, currency: str, country_code: str):
    """没有 port 信号时, currency 应当推出对应的 country。"""
    # 同时插多个 country, 确保选择是基于 currency map 而不是"挑第一个"
    countries = {
        "JPN": "Japan",
        "USA": "United States",
        "AUS": "Australia",
        "THA": "Thailand",
        "CHN": "China",
        "GBR": "United Kingdom",
        "SGP": "Singapore",
    }
    inserted = {code: _add_country(db, name=name, code=code) for code, name in countries.items()}

    order = _make_order(currency=currency)
    result = geo.resolve_geo(order, db)

    assert result["country_id"] == inserted[country_code].id
    assert result["port_id"] is None  # 没有 port 信号


def test_eur_does_not_resolve_country(db):
    """EUR 在 _CURRENCY_TO_COUNTRY 里映射到 None (歧义), 不应当猜国家。"""
    _add_country(db, name="Germany", code="DEU")
    _add_country(db, name="France", code="FRA")

    order = _make_order(currency="EUR")
    result = geo.resolve_geo(order, db)
    assert result["country_id"] is None


def test_unknown_currency_returns_none(db):
    """不在 map 里的货币不应该崩, 也不应该乱猜。"""
    _add_country(db, name="Brazil", code="BRA")
    order = _make_order(currency="BRL")  # BRL 不在 _CURRENCY_TO_COUNTRY 里
    result = geo.resolve_geo(order, db)
    assert result["country_id"] is None


# ─── Geo resolver: delivery date passthrough ──────────────────


def test_delivery_date_from_order_column(db):
    """order.delivery_date 已经填了时, 直接用它。"""
    order = _make_order(delivery_date="2026-05-20")
    result = geo.resolve_geo(order, db)
    assert result["delivery_date"] == "2026-05-20"


def test_delivery_date_falls_back_to_metadata_extras(db):
    """主字段没填, 但 metadata.extra_fields.loading_date 有值 — fallback 上来。"""
    order = _make_order(metadata={"extra_fields": {"loading_date": "2026-06-01"}})
    result = geo.resolve_geo(order, db)
    assert result["delivery_date"] == "2026-06-01"


# ─── code_first.match_by_code ─────────────────────────────────


def _fake_product(**kwargs) -> SimpleNamespace:
    """Build a Product-like stub. Only attributes match_by_code reads."""
    defaults = {
        "id": 1,
        "code": "TEST-1",
        "product_name_en": "Test Product",
        "product_name_jp": None,
        "price": None,
        # R2 / Felix 2026-06-06 — _serialize_db_product reads
        # contract_price into matched_product; stub needs the attr.
        "contract_price": None,
        "currency": "USD",
        "supplier_id": None,
        "category_id": None,
        "pack_size": None,
        "unit_size": None,
        "unit": "KG",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_code_first_exact_match_returns_matched():
    """输入 product_code 与池里某个 Product.code 完全相等 → matched."""
    pool = [_fake_product(id=10, code="ABC-123", product_name_en="Apple")]
    inputs = [{"product_code": "ABC-123", "product_name": "Apple", "quantity": 5}]

    all_results, unmatched = code_first.match_by_code(inputs, pool)

    assert len(all_results) == 1
    r = all_results[0]
    assert r["match_status"] == "matched"
    assert r["match_score"] == 1.0
    assert r["matched_product"]["id"] == 10
    assert r["matched_product"]["code"] == "ABC-123"
    assert unmatched == []


def test_code_first_match_is_case_insensitive():
    """输入小写 / 池大写, 都要匹中 (`code.upper()`)."""
    pool = [_fake_product(id=11, code="ABC-123")]
    inputs = [{"product_code": "abc-123", "product_name": "Apple", "quantity": 1}]

    all_results, _ = code_first.match_by_code(inputs, pool)
    assert all_results[0]["match_status"] == "matched"
    assert all_results[0]["matched_product"]["id"] == 11


def test_code_first_no_match_returns_not_matched_and_in_unmatched():
    """池里没这个 code → not_matched, 并出现在 unmatched 列表里待 LLM 处理。"""
    pool = [_fake_product(id=12, code="XYZ-9")]
    inputs = [{"product_code": "NOPE", "product_name": "Lost", "quantity": 1}]

    all_results, unmatched = code_first.match_by_code(inputs, pool)

    assert all_results[0]["match_status"] == "not_matched"
    assert all_results[0]["match_score"] == 0.0
    assert all_results[0]["matched_product"] is None
    assert len(unmatched) == 1
    assert unmatched[0] is inputs[0]


def test_code_first_empty_product_code_is_unmatched():
    """没填 product_code 的输入也算 unmatched (而不是崩)."""
    pool = [_fake_product(id=13, code="X1")]
    inputs = [{"product_code": "", "product_name": "Whoops", "quantity": 1}]

    all_results, unmatched = code_first.match_by_code(inputs, pool)
    assert all_results[0]["match_status"] == "not_matched"
    assert len(unmatched) == 1


def test_code_first_preserves_input_order():
    """all_results 顺序必须跟 inputs 一致 — 下游表格依赖这点."""
    pool = [_fake_product(id=20, code="B"), _fake_product(id=21, code="C")]
    inputs = [
        {"product_code": "A", "product_name": "first", "quantity": 1},
        {"product_code": "B", "product_name": "second", "quantity": 1},
        {"product_code": "C", "product_name": "third", "quantity": 1},
    ]

    all_results, _ = code_first.match_by_code(inputs, pool)

    assert [r["product_code"] for r in all_results] == ["A", "B", "C"]
    assert all_results[0]["match_status"] == "not_matched"
    assert all_results[1]["match_status"] == "matched"
    assert all_results[2]["match_status"] == "matched"


def test_code_first_manual_product_id_wins_over_source_code():
    """人工关联只能命中当前候选池，但命中后应优先于 PO 原编码。"""
    pool = [
        _fake_product(id=30, code="OLD-CODE"),
        _fake_product(id=31, code="MANUAL-CODE", product_name_en="Manual Product"),
    ]
    inputs = [
        {
            "product_code": "OLD-CODE",
            "product_name": "PO Name",
            "quantity": 1,
            "manual_product_id": 31,
        }
    ]

    all_results, unmatched = code_first.match_by_code(inputs, pool)

    assert unmatched == []
    assert all_results[0]["match_status"] == "matched"
    assert all_results[0]["matched_product"]["id"] == 31
    assert all_results[0]["match_reason"] == "人工关联商品"
    assert all_results[0]["manual_product_id"] == 31


def test_code_first_manual_product_outside_pool_stays_unmatched_without_fuzzy_fallback():
    """过期或越过港口范围的人工 ID 不能伪造成成功，也不能再交给模糊匹配。"""
    pool = [_fake_product(id=40, code="IN-SCOPE", product_name_en="Same Product Name")]
    inputs = [
        {
            "product_code": "IN-SCOPE",
            "product_name": "Same Product Name",
            "quantity": 1,
            "manual_product_id": 999,
        }
    ]

    all_results, unmatched = code_first.match_by_code(inputs, pool)

    assert unmatched == []
    assert all_results[0]["match_status"] == "not_matched"
    assert all_results[0]["matched_product"] is None
    assert all_results[0]["match_reason"] == "人工关联商品不在当前港口或有效期候选范围内"


def test_code_first_batch_matching_across_many_products():
    """50 条输入混合命中/不命中 — 计数正确, 结果数量等于输入数量."""
    pool = [_fake_product(id=i, code=f"P{i}", product_name_en=f"Prod{i}") for i in range(25)]
    inputs = []
    expected_matched = 0
    for i in range(50):
        code = f"P{i}" if i < 25 else f"UNK{i}"  # 前 25 命中, 后 25 不命中
        if i < 25:
            expected_matched += 1
        inputs.append({"product_code": code, "product_name": f"x{i}", "quantity": 1})

    all_results, unmatched = code_first.match_by_code(inputs, pool)

    assert len(all_results) == 50
    matched = sum(1 for r in all_results if r["match_status"] == "matched")
    assert matched == expected_matched == 25
    assert len(unmatched) == 25


# ─── FakeMatcher: fuzzy name match ────────────────────────────


def test_fake_matcher_picks_overlapping_name():
    """3 token 重叠 → jaccard ≈ 0.6 → 触发命中。"""
    pool = [_fake_product(id=100, code="X", product_name_en="organic green tea bag")]
    unmatched = [{"product_name": "organic green tea", "quantity": 1}]

    picks = FakeMatcher().refine(unmatched, pool)
    assert picks == {0: 100}


def test_fake_matcher_below_jaccard_threshold_returns_no_pick():
    """重叠不够 → 不命中. `organic` (1 个 token) 和 5 个 token 的池 → jaccard
    ≈ 1/5 = 0.2 < 0.34, 而且重叠 1 < MIN_OVERLAP 2."""
    pool = [_fake_product(id=200, code="X", product_name_en="apple pie large slice gallons")]
    unmatched = [{"product_name": "apple banana", "quantity": 1}]
    # 重叠只有 "apple" 一个 token → 不达 MIN_OVERLAP=2
    assert FakeMatcher().refine(unmatched, pool) == {}


def test_fake_matcher_requires_minimum_overlap():
    """即使 jaccard 高, 也要至少 2 个 token 重叠。"""
    # 输入只有 1 个 token, MIN_OVERLAP 不可能达到
    pool = [_fake_product(id=300, code="X", product_name_en="rice")]
    unmatched = [{"product_name": "rice", "quantity": 1}]
    # 输入 tokens 只有 {"rice"} 长度 1 < 2 (`if len(input_tokens) < 2: continue`)
    assert FakeMatcher().refine(unmatched, pool) == {}


def test_fake_matcher_returns_empty_for_empty_unmatched():
    """边界: 空 unmatched 列表 — 返回空字典."""
    assert FakeMatcher().refine([], []) == {}


# ─── LLM refinement: only upgrades, never downgrades ──────────


def test_apply_refinement_upgrades_only_not_matched():
    """关键不变量: matched 条目永远不会被 refinement 改动."""
    pool = [_fake_product(id=500, code="P5", product_name_en="strong matching name")]
    # all_results 结构: 第 0 条已经 matched, 第 1 条 not_matched
    all_results = [
        {
            "product_code": "EXACT",
            "product_name": "completely unrelated string here",
            "quantity": 1,
            "match_status": "matched",
            "match_score": 1.0,
            "match_reason": "产品代码完全匹配",
            "matched_product": {"id": 999, "code": "EXACT"},
        },
        {
            "product_code": "",
            "product_name": "strong matching name",
            "quantity": 1,
            "match_status": "not_matched",
            "match_score": 0.0,
            "match_reason": "",
            "matched_product": None,
        },
    ]
    unmatched_inputs = [{"product_name": "strong matching name", "quantity": 1}]

    llm_refine.apply_refinement(all_results, unmatched_inputs, pool)

    # 第 0 条 (已 matched) 完全没动
    assert all_results[0]["match_status"] == "matched"
    assert all_results[0]["match_score"] == 1.0
    assert all_results[0]["matched_product"]["id"] == 999  # 仍然指向原来的产品

    # 第 1 条 (not_matched) 被升级
    assert all_results[1]["match_status"] == "matched"
    assert all_results[1]["match_score"] == 0.7
    assert "模糊匹配" in all_results[1]["match_reason"]
    assert all_results[1]["matched_product"]["id"] == 500


def test_apply_refinement_no_picks_leaves_results_unchanged():
    """matcher 一个都没匹中 → all_results 一字不动。"""
    pool = [_fake_product(id=501, code="X", product_name_en="completely different name here")]
    all_results = [
        {
            "product_code": "",
            "product_name": "totally other words",
            "quantity": 1,
            "match_status": "not_matched",
            "match_score": 0.0,
            "match_reason": "",
            "matched_product": None,
        },
    ]
    unmatched_inputs = [{"product_name": "totally other words", "quantity": 1}]

    snapshot = {**all_results[0]}
    llm_refine.apply_refinement(all_results, unmatched_inputs, pool)
    assert all_results[0] == snapshot


def test_apply_refinement_empty_unmatched_is_noop():
    """没有任何 unmatched → 早退, 不调 matcher."""
    all_results = [
        {
            "product_code": "X",
            "product_name": "y",
            "quantity": 1,
            "match_status": "matched",
            "match_score": 1.0,
            "match_reason": "exact",
            "matched_product": {"id": 1},
        }
    ]
    llm_refine.apply_refinement(all_results, [], pool=[])
    assert all_results[0]["match_status"] == "matched"


# ─── service.run_matching: end-to-end ─────────────────────────


def test_run_matching_end_to_end_code_hits(db):
    """完整跑一遍 service.run_matching: 输入 1 个 code 完全匹配的产品,
    pool 来自 seed_product, 结果应当 matched + statistics 100%."""
    seed_user(db, email="match-e2e@example.com")
    # seed master geo
    jp = _add_country(db, name="Japan", code="JPN")
    _add_port(db, name="Yokohama", country_id=jp.id)
    # seed product in pool
    seed_product(db, code="SOY-100", name="Soy Sauce 1L", country_id=jp.id)

    order = _make_order(
        currency="JPY",
        products=[{"product_code": "SOY-100", "product_name": "Soy Sauce", "quantity": 12}],
    )
    order.id = 99  # for log line — service.run_matching reads order.id

    result = service.run_matching(order, db)

    assert result["statistics"] == {
        "total": 1,
        "matched": 1,
        "not_matched": 0,
        "match_rate": 100.0,
    }
    assert order.country_id == jp.id  # currency → JPN
    assert order.match_results[0]["match_status"] == "matched"
    assert order.match_results[0]["matched_product"]["code"] == "SOY-100"


def test_run_matching_writes_country_port_back_to_order(db):
    """run_matching 必须把 geo 解析结果写回 Order 列 (caller 后面会 commit)."""
    seed_user(db, email="match-geo@example.com")
    us = _add_country(db, name="United States", code="USA")
    miami = _add_port(db, name="Miami", country_id=us.id)

    order = _make_order(destination_port="Miami", products=[])
    order.id = 100
    service.run_matching(order, db)

    assert order.country_id == us.id
    assert order.port_id == miami.id


def test_run_matching_missing_delivery_date_sets_skipped_reason(db):
    """没 delivery_date → processing_error 写 "missing_delivery_date" 让上游
    知道为什么 effective window 没过滤。"""
    seed_user(db, email="match-nodate@example.com")
    order = _make_order(products=[])
    order.id = 101
    result = service.run_matching(order, db)
    assert result["skipped_reason"] == "missing_delivery_date"
    assert order.processing_error == "missing_delivery_date"


def test_run_matching_statistics_for_mixed_results(db):
    """混合: 一个 code 命中, 一个完全找不到 — 统计应该 1/2 = 50%."""
    seed_user(db, email="match-mix@example.com")
    jp = _add_country(db, name="Japan", code="JPN")
    seed_product(db, code="HIT-1", name="Hit Product", country_id=jp.id)

    order = _make_order(
        currency="JPY",
        products=[
            {"product_code": "HIT-1", "product_name": "Hit", "quantity": 1},
            {"product_code": "MISS-X", "product_name": "Totally Unrelated Words", "quantity": 1},
        ],
    )
    order.id = 102
    service.run_matching(order, db)

    stats = order.match_statistics
    assert stats["total"] == 2
    assert stats["matched"] == 1
    assert stats["not_matched"] == 1
    assert stats["match_rate"] == 50.0


def test_run_matching_empty_products_yields_zero_stats(db):
    """空 products 列表 — 不应该崩, 统计是 0/0 / 0%."""
    seed_user(db, email="match-empty@example.com")
    order = _make_order(delivery_date="2026-05-20", products=[])
    order.id = 103
    service.run_matching(order, db)

    assert order.match_statistics == {
        "total": 0,
        "matched": 0,
        "not_matched": 0,
        "match_rate": 0.0,
    }
