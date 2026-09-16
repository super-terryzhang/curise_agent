"""Section 4 — Orders: agent tools (narrow `get_order_detail` family).

测试目标：
    Agent 调订单工具时返回的 JSON 必须满足三条契约：
      1. 大小可控：每个工具 response ≤ 8 KB（远低于 general-agent 16 KB
         硬截断），即便订单含 100+ 产品也不被截。
      2. 投影正确：metadata-only / products / stats 各管一摊，不重叠。
      3. 过滤 + 分页：`list_order_products` 支持中英日 substring + match
         status 过滤；offset/limit 分页带 `has_more` 信号。

为什么重要：
    2026-05-14 真实事故：用户问"订单 103 里有什么蔬菜"，旧 `get_order_detail`
    把整个 OrderDetail dump 到字符串、被截到 16K，agent 真没看见产品列表。
    诊断后按 Anthropic 工具设计准则拆成 3 个窄工具 —— 这些测试 pin 住
    新行为，确保未来重写 OrderDetail schema 时不静默回退。

设计方法：
    - 直接走 REGISTRY.dispatch (跟生产同一条码路径)
    - in-memory DB + 真实 ORM rows (Order + Product + Category +
      Supplier)，匹配结果按 `code_first._serialize_db_product` 的真实
      shape 构造
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from general_agent import REGISTRY, ToolContext

from agent.runtime import tools as _v3_tools  # noqa: F401 — register
from agent.runtime.deps import V3Deps, inject_deps
from domains.masterdata.models import Category, Country, Port, Product, Supplier
from domains.orders.models import Order
from test_v2.fixtures.helpers import seed_user


# ─── Helpers ──────────────────────────────────────────────────


def _ctx(db, *, user_id: int, role: str = "superadmin") -> ToolContext:
    ctx = ToolContext(workspace=Path("/tmp"), extras={})
    inject_deps(ctx, V3Deps(db=db, user_id=user_id, user_role=role))
    return ctx


def _seed_masterdata(db) -> dict[str, dict[str, int]]:
    """Insert categories + suppliers + countries + ports; return id maps."""
    db.add_all(
        [
            Category(name="FRUIT"),
            Category(name="VEGETABLE"),
            Category(name="MEAT"),
        ]
    )
    db.add_all(
        [
            Supplier(name="Sunkist"),
            Supplier(name="LocalFarm"),
        ]
    )
    db.add(Country(name="USA"))
    db.add(Port(name="YOK"))
    db.commit()
    cats = {c.name: c.id for c in db.query(Category).all()}
    sups = {s.name: s.id for s in db.query(Supplier).all()}
    return {"category": cats, "supplier": sups}


def _seed_products(db, ids: dict) -> dict[str, int]:
    """Insert one DB Product per category so match_results.matched_product
    can reference real ids. Returns code → product_id."""
    db.add_all(
        [
            Product(
                product_name_en="Apple Red",
                code="FRT-APL",
                category_id=ids["category"]["FRUIT"],
                supplier_id=ids["supplier"]["Sunkist"],
                price=10.0,
                unit="CT",
            ),
            Product(
                product_name_en="Carrot",
                code="VEG-CAR",
                category_id=ids["category"]["VEGETABLE"],
                supplier_id=ids["supplier"]["LocalFarm"],
                price=5.0,
                unit="KG",
            ),
            Product(
                product_name_en="Beef Tenderloin",
                code="MEAT-BEEF",
                category_id=ids["category"]["MEAT"],
                supplier_id=ids["supplier"]["LocalFarm"],
                price=50.0,
                unit="KG",
            ),
        ]
    )
    db.commit()
    return {p.code: p.id for p in db.query(Product).all()}


def _make_match_row(
    *,
    code: str,
    name: str,
    qty: int,
    unit: str,
    price: float,
    status: str,
    matched: Product | None = None,
) -> dict:
    """Construct one match_results dict in the same shape `code_first.py`
    builds them — keeps test fixtures realistic."""
    row = {
        "product_code": code,
        "product_name": name,
        "quantity": qty,
        "unit": unit,
        "unit_price": price,
        "match_status": status,
        "match_score": 1.0 if status == "matched" else 0.0,
        "match_reason": "exact" if status == "matched" else "",
    }
    if matched is not None:
        row["matched_product"] = {
            "id": matched.id,
            "code": matched.code,
            "product_name_en": matched.product_name_en,
            "product_name_jp": matched.product_name_jp,
            "price": float(matched.price) if matched.price else None,
            "currency": matched.currency,
            "supplier_id": matched.supplier_id,
            "category_id": matched.category_id,
            "pack_size": matched.pack_size,
            "unit_size": matched.unit_size,
            "unit": matched.unit,
        }
    else:
        row["matched_product"] = None
    return row


@pytest.fixture
def seeded_order(db, seed_user):
    """An order with 3 line items: 1 matched fruit, 1 matched vegetable,
    1 unmatched. FK masterdata is pre-seeded so name lookups work."""
    ids = _seed_masterdata(db)
    _seed_products(db, ids)
    fruit_db = db.query(Product).filter(Product.code == "FRT-APL").one()
    veg_db = db.query(Product).filter(Product.code == "VEG-CAR").one()

    order = Order(
        user_id=seed_user.id,
        filename="test-order.pdf",
        file_type="pdf",
        status="ready_for_review",
        po_number="PO-T1",
        ship_name="MV Test",
        currency="USD",
        delivery_date="2026-06-01",
        match_results=[
            _make_match_row(
                code="FRT-APL", name="Apple Red Delicious",
                qty=10, unit="CT", price=12.0,
                status="matched", matched=fruit_db,
            ),
            _make_match_row(
                code="VEG-CAR", name="Carrot Baby",
                qty=20, unit="KG", price=6.0,
                status="matched", matched=veg_db,
            ),
            _make_match_row(
                code="UNK-001", name="Mystery Item",
                qty=5, unit="EA", price=99.0,
                status="not_matched", matched=None,
            ),
        ],
        product_count=3,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


# ─── get_order_detail (slim) ─────────────────────────────────


def test_get_order_detail_returns_metadata_only(db, seed_user, seeded_order):
    """Slim shape: no products / match_results / extraction_data fields.
    Anything else means the old fat dump came back."""
    out = REGISTRY.view(["get_order_detail"]).dispatch(
        "get_order_detail", {"order_id": seeded_order.id}, ctx=_ctx(db, user_id=seed_user.id)
    )
    assert not out.startswith("Error:"), out
    payload = json.loads(out)
    # Core metadata present
    assert payload["id"] == seeded_order.id
    assert payload["po_number"] == "PO-T1"
    assert payload["ship_name"] == "MV Test"
    assert payload["product_count"] == 3
    assert payload["has_match_results"] is True
    # Fat fields removed
    for forbidden in (
        "products",
        "match_results",
        "extraction_data",
        "anomaly_data",
        "inquiry_data",
        "delivery_data",
        "financial_data",
    ):
        assert forbidden not in payload, (
            f"`{forbidden}` leaked back into get_order_detail — the "
            f"slim refactor was supposed to push it to a narrow tool"
        )


def test_get_order_detail_fits_in_under_8kb(db, seed_user, seeded_order):
    """Token-efficiency contract: ≤ 8 KB (much less than 16 KB cap)."""
    out = REGISTRY.view(["get_order_detail"]).dispatch(
        "get_order_detail", {"order_id": seeded_order.id}, ctx=_ctx(db, user_id=seed_user.id)
    )
    assert len(out) <= 8000, f"get_order_detail size {len(out)} > 8000"


# ─── list_order_products ─────────────────────────────────────


def test_list_order_products_returns_all_when_no_filter(db, seed_user, seeded_order):
    out = REGISTRY.view(["list_order_products"]).dispatch(
        "list_order_products",
        {"order_id": seeded_order.id},
        ctx=_ctx(db, user_id=seed_user.id),
    )
    payload = json.loads(out)
    assert payload["total_in_order"] == 3
    assert payload["total_matching_filter"] == 3
    assert payload["returned"] == 3
    assert payload["has_more"] is False
    # FK names resolved
    fruit_row = next(i for i in payload["items"] if i["product_code"] == "FRT-APL")
    assert fruit_row["category_name"] == "FRUIT"
    assert fruit_row["supplier_name"] == "Sunkist"
    assert fruit_row["matched_name_en"] == "Apple Red"
    # Unmatched row has no category/supplier names but still appears
    unmatched_row = next(i for i in payload["items"] if i["product_code"] == "UNK-001")
    assert unmatched_row["category_name"] is None
    assert unmatched_row["match_status"] == "not_matched"


def test_list_order_products_filters_by_chinese_category_query(db, seed_user, seeded_order):
    """User asks "蔬菜" → tool finds the carrot row via category_name match."""
    # NB: our test seeded categories in English (FRUIT/VEGETABLE/MEAT) —
    # the query parameter does substring match, so "VEGETABLE" works.
    out = REGISTRY.view(["list_order_products"]).dispatch(
        "list_order_products",
        {"order_id": seeded_order.id, "query": "VEGETABLE"},
        ctx=_ctx(db, user_id=seed_user.id),
    )
    payload = json.loads(out)
    assert payload["total_matching_filter"] == 1
    assert payload["items"][0]["product_code"] == "VEG-CAR"


def test_list_order_products_filters_by_match_status(db, seed_user, seeded_order):
    out = REGISTRY.view(["list_order_products"]).dispatch(
        "list_order_products",
        {"order_id": seeded_order.id, "match_status": "not_matched"},
        ctx=_ctx(db, user_id=seed_user.id),
    )
    payload = json.loads(out)
    assert payload["total_matching_filter"] == 1
    assert payload["items"][0]["product_code"] == "UNK-001"


def test_list_order_products_pagination_has_more_signal(db, seed_user, seeded_order):
    out = REGISTRY.view(["list_order_products"]).dispatch(
        "list_order_products",
        {"order_id": seeded_order.id, "limit": 2, "offset": 0},
        ctx=_ctx(db, user_id=seed_user.id),
    )
    payload = json.loads(out)
    assert payload["returned"] == 2
    assert payload["has_more"] is True
    # Page 2
    out2 = REGISTRY.view(["list_order_products"]).dispatch(
        "list_order_products",
        {"order_id": seeded_order.id, "limit": 2, "offset": 2},
        ctx=_ctx(db, user_id=seed_user.id),
    )
    payload2 = json.loads(out2)
    assert payload2["returned"] == 1
    assert payload2["has_more"] is False


def test_list_order_products_rejects_unknown_match_status(db, seed_user, seeded_order):
    out = REGISTRY.view(["list_order_products"]).dispatch(
        "list_order_products",
        {"order_id": seeded_order.id, "match_status": "garbage"},
        ctx=_ctx(db, user_id=seed_user.id),
    )
    assert out.startswith("Error:")
    assert "match_status" in out


def test_list_order_products_size_under_8kb_with_100_rows(db, seed_user):
    """Realistic stress: build an order with 100 match rows and ensure the
    full unpaginated response stays under 8 KB (with default limit=20)."""
    ids = _seed_masterdata(db)
    _seed_products(db, ids)
    fruit_db = db.query(Product).filter(Product.code == "FRT-APL").one()
    rows = [
        _make_match_row(
            code=f"FRT-{i:03d}", name=f"Item {i}", qty=i, unit="EA",
            price=float(i), status="matched", matched=fruit_db,
        )
        for i in range(100)
    ]
    order = Order(
        user_id=seed_user.id,
        filename="big.pdf",
        file_type="pdf",
        status="ready_for_review",
        match_results=rows,
        product_count=100,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    out = REGISTRY.view(["list_order_products"]).dispatch(
        "list_order_products",
        {"order_id": order.id},  # default limit=20
        ctx=_ctx(db, user_id=seed_user.id),
    )
    payload = json.loads(out)
    assert payload["total_in_order"] == 100
    assert payload["returned"] == 20
    assert payload["has_more"] is True
    assert len(out) <= 8000, f"size {len(out)} > 8000 with 20 rows"


# ─── get_order_match_stats ───────────────────────────────────


def test_get_order_match_stats_counts_categories_correctly(db, seed_user, seeded_order):
    out = REGISTRY.view(["get_order_match_stats"]).dispatch(
        "get_order_match_stats",
        {"order_id": seeded_order.id},
        ctx=_ctx(db, user_id=seed_user.id),
    )
    payload = json.loads(out)
    assert payload["order_id"] == seeded_order.id
    assert payload["total"] == 3
    assert payload["matched"] == 2
    assert payload["not_matched"] == 1
    assert payload["possible_match"] == 0
    # 2 matched out of 3 = 66.7%
    assert payload["match_rate_pct"] == 66.7
    # avg confidence: (1.0 + 1.0 + 0.0) / 3 = 0.667
    assert payload["avg_confidence"] == 0.667


def test_get_order_match_stats_size_under_500_bytes(db, seed_user, seeded_order):
    out = REGISTRY.view(["get_order_match_stats"]).dispatch(
        "get_order_match_stats",
        {"order_id": seeded_order.id},
        ctx=_ctx(db, user_id=seed_user.id),
    )
    assert len(out) <= 500, f"match_stats size {len(out)} > 500"


def test_get_order_match_stats_on_empty_order(db, seed_user):
    """An order with no match_results: counters zero, match_rate=0,
    avg_confidence null."""
    order = Order(
        user_id=seed_user.id,
        filename="empty.pdf",
        file_type="pdf",
        status="pending",
        match_results=[],
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    out = REGISTRY.view(["get_order_match_stats"]).dispatch(
        "get_order_match_stats",
        {"order_id": order.id},
        ctx=_ctx(db, user_id=seed_user.id),
    )
    payload = json.loads(out)
    assert payload["total"] == 0
    assert payload["match_rate_pct"] == 0.0
    assert payload["avg_confidence"] is None


# ─── Cross-user isolation ────────────────────────────────────


def test_list_order_products_nonexistent_actor_is_rejected(db, seed_user, seeded_order):
    """The auth-hardening registry rejects nonexistent actor 999 before reading."""
    out = REGISTRY.view(["list_order_products"]).dispatch(
        "list_order_products",
        {"order_id": seeded_order.id},
        ctx=_ctx(db, user_id=999, role="employee"),
    )
    assert out.startswith("[tool-error] AccountInactive:")
