"""Section 6 — Masterdata: CRUD service for countries / categories / ports /
suppliers / products / exchange rates.

测试目标：
    `domains/masterdata/service.py` 是所有 master data 写入的单一入口。
    本文件锁定它的对外行为：
    1. `search_*` / `list_products` 的 `{total, items}` 形状（2026-05 重构后的契约）。
    2. denormalize 的 country_name / category_name / supplier_name / port_name 必须填好。
    3. 外键校验、唯一性冲突、引用阻挡删除等领域错误必须用 `MasterdataError` 子类型抛出，
       绝不能让裸 `IntegrityError` 漏到 HTTP 层。
    4. 汇率抓取（`fetch_exchange_rates`）走外部 HTTP，必须接受注入的 client 以便测试。

为什么重要：
    HTTP 层 (`apps/http/masterdata.py`) + Agent 工具层都直接依赖这里返回的形状。
    一旦 `{total, items}` 退化为裸 list、或 denormalized 字段忘了填，前端 N 处分页/筛选
    就会瞬间坏掉，也无法回答"我有多少供应商"这种统计问题。

设计方法：
    使用 conftest 提供的 in-memory SQLite + 真实的 service 函数（不 mock service）。
    外部 HTTP 通过 `httpx.MockTransport` 注入到 `fetch_exchange_rates(..., client=...)`。
    每个测试一个具体行为，断言具体字段值。
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from domains.masterdata import service
from domains.masterdata.schemas import (
    CategoryCreate,
    CountryCreate,
    ExchangeRateCreate,
    ExchangeRateUpdate,
    PortCreate,
    ProductCreate,
    ProductUpdate,
    SupplierCreate,
)


# ─── {total, items} contract — search_* + list_products ───────


@pytest.mark.parametrize(
    ("entity", "create_calls", "search_fn"),
    [
        (
            "countries",
            [lambda db: service.create_country(db, CountryCreate(name="Japan", code="JP")),
             lambda db: service.create_country(db, CountryCreate(name="USA", code="US"))],
            lambda db: service.search_countries(db),
        ),
        (
            "categories",
            [lambda db: service.create_category(db, CategoryCreate(name="Meat", code="MEAT")),
             lambda db: service.create_category(db, CategoryCreate(name="Dairy", code="DAIRY"))],
            lambda db: service.search_categories(db),
        ),
        (
            "ports",
            [lambda db: service.create_port(db, PortCreate(name="Tokyo")),
             lambda db: service.create_port(db, PortCreate(name="Osaka"))],
            lambda db: service.search_ports(db),
        ),
        (
            "suppliers",
            [lambda db: service.create_supplier(db, SupplierCreate(name="ACME")),
             lambda db: service.create_supplier(db, SupplierCreate(name="Globex"))],
            lambda db: service.search_suppliers(db),
        ),
    ],
)
def test_search_returns_total_items_shape(db, entity, create_calls, search_fn):
    """search_* must return the {total: int, items: list} envelope.

    The HTTP/Agent layer relies on this shape to answer "how many X exist".
    """
    # Snapshot the starting count — section_6's autouse fixture pre-seeds
    # a default Country + Port (for upload tests' strict identity
    # contract), so absolute counts are not zero here. We assert the
    # DELTA matches what we created.
    before = search_fn(db)["total"]

    for fn in create_calls:
        fn(db)

    result = search_fn(db)
    assert isinstance(result, dict), f"{entity} search must return a dict"
    assert set(result.keys()) >= {"total", "items"}, f"{entity} missing total/items keys"
    assert isinstance(result["total"], int)
    assert isinstance(result["items"], list)
    assert result["total"] == before + 2
    assert len(result["items"]) == before + 2


def test_list_products_returns_total_items_envelope(db):
    """list_products returns {total, items} — never a bare list."""
    service.create_product(db, ProductCreate(product_name_en="Beef"))
    service.create_product(db, ProductCreate(product_name_en="Chicken"))

    result = service.list_products(db)
    assert set(result.keys()) >= {"total", "items"}
    assert result["total"] == 2
    assert len(result["items"]) == 2


def test_list_exchange_rates_returns_bare_list(db):
    """`list_exchange_rates` is intentionally a flat list — exchange rate listings
    are small enough that pagination isn't needed. Locking this contract so a
    careless "consistency" refactor doesn't break HTTP / Agent callers."""
    service.create_exchange_rate(
        db,
        ExchangeRateCreate(
            from_currency="USD", to_currency="JPY", rate=150.0, effective_date=date(2026, 4, 24)
        ),
    )
    rows = service.list_exchange_rates(db)
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["from_currency"] == "USD"


# ─── search filters + pagination semantics ──────────────────


def test_list_products_search_filter_narrows_items(db):
    """When search filters the result set, `total` reflects the FILTERED count
    (not the table-wide count) and `items` is the filtered slice."""
    service.create_product(db, ProductCreate(product_name_en="Beef Ribeye"))
    service.create_product(db, ProductCreate(product_name_en="Chicken Breast"))
    service.create_product(db, ProductCreate(product_name_en="Beef Brisket"))

    result = service.list_products(db, search="beef")
    assert result["total"] == 2
    names = sorted(p["product_name_en"] for p in result["items"])
    assert names == ["Beef Brisket", "Beef Ribeye"]


def test_list_products_total_is_true_count_not_items_length(db):
    """The fundamental "how many products" test. 12 products in DB + limit=5 →
    total=12 (the count), items has 5 rows. This is the bug-class the 2026-05
    refactor exists to prevent."""
    for i in range(12):
        service.create_product(db, ProductCreate(product_name_en=f"Product {i:02d}"))

    result = service.list_products(db, limit=5)
    assert result["total"] == 12
    assert len(result["items"]) == 5


def test_search_suppliers_with_limit_caps_items_but_keeps_full_total(db):
    """20 suppliers + limit=3 → total=20, items length=3.
    `total` answers "how many", `items` is just the page the UI wants to render."""
    for i in range(20):
        service.create_supplier(db, SupplierCreate(name=f"Supplier {i:02d}"))

    result = service.search_suppliers(db, limit=3)
    assert result["total"] == 20
    assert len(result["items"]) == 3


# ─── Product denormalized parent names ──────────────────────


def test_create_product_populates_denormalized_parent_names(db):
    """Product serialization includes country_name / category_name / supplier_name /
    port_name so the frontend never has to fetch parents separately."""
    country = service.create_country(db, CountryCreate(name="Japan", code="JP"))
    category = service.create_category(db, CategoryCreate(name="Seafood"))
    supplier = service.create_supplier(db, SupplierCreate(name="Tsukiji Fish Co"))
    port = service.create_port(db, PortCreate(name="Yokohama"))

    p = service.create_product(
        db,
        ProductCreate(
            product_name_en="Tuna Otoro",
            code="TUNA-001",
            country_id=country["id"],
            category_id=category["id"],
            supplier_id=supplier["id"],
            port_id=port["id"],
            price=120.0,
            currency="JPY",
        ),
    )
    assert p["country_name"] == "Japan"
    assert p["category_name"] == "Seafood"
    assert p["supplier_name"] == "Tsukiji Fish Co"
    assert p["port_name"] == "Yokohama"
    assert p["price"] == 120.0


def test_create_product_with_unknown_fk_raises_bad_request(db):
    """Unknown country/category/supplier/port → BadRequest, NEVER a raw
    IntegrityError bubbling up to the HTTP layer."""
    with pytest.raises(service.BadRequest):
        service.create_product(
            db, ProductCreate(product_name_en="Orphan", country_id=99999)
        )


def test_update_product_changes_mutable_fields(db):
    """update_product mutates only the supplied fields and returns the new shape."""
    p = service.create_product(
        db, ProductCreate(product_name_en="Old Name", price=10.0, unit="kg")
    )

    updated = service.update_product(
        db, p["id"], ProductUpdate(product_name_en="New Name", price=25.5)
    )
    assert updated["product_name_en"] == "New Name"
    assert updated["price"] == 25.5
    # Unspecified field preserved
    assert updated["unit"] == "kg"


# ─── Reference-based delete protection (the 409 path) ──────


def test_delete_country_with_referencing_port_raises_conflict(db):
    """Country has a Port child → delete must surface Conflict (HTTP 409)
    rather than DB cascade-deleting children silently."""
    c = service.create_country(db, CountryCreate(name="Japan", code="JP"))
    service.create_port(db, PortCreate(name="Tokyo", country_id=c["id"]))

    with pytest.raises(service.Conflict) as exc:
        service.delete_country(db, c["id"])
    assert "港口" in str(exc.value)


def test_delete_country_with_referencing_supplier_raises_conflict(db):
    """Same as port, but via Supplier — verifies the multi-ref check loop."""
    c = service.create_country(db, CountryCreate(name="Japan", code="JP"))
    service.create_supplier(db, SupplierCreate(name="Tokyo Foods", country_id=c["id"]))

    with pytest.raises(service.Conflict) as exc:
        service.delete_country(db, c["id"])
    assert "供应商" in str(exc.value)


def test_delete_supplier_with_referencing_product_raises_conflict(db):
    """Cross-domain ref guard: Supplier still in use by a Product blocks delete."""
    s = service.create_supplier(db, SupplierCreate(name="ACME"))
    service.create_product(
        db, ProductCreate(product_name_en="Hammer", supplier_id=s["id"])
    )
    with pytest.raises(service.Conflict):
        service.delete_supplier(db, s["id"])


# ─── Exchange rate manual create / update ──────────────────


def test_exchange_rate_manual_create_uppercases_codes(db):
    """ISO currency codes are forced uppercase to keep the unique constraint
    `(from, to, date)` deterministic."""
    r = service.create_exchange_rate(
        db,
        ExchangeRateCreate(
            from_currency="usd",
            to_currency="jpy",
            rate=150.0,
            effective_date=date(2026, 5, 1),
        ),
    )
    assert r["from_currency"] == "USD"
    assert r["to_currency"] == "JPY"
    assert r["rate"] == 150.0
    assert r["source"] == "manual"


def test_exchange_rate_manual_update_changes_rate(db):
    """Manual update is the second half of the upsert path."""
    r = service.create_exchange_rate(
        db,
        ExchangeRateCreate(
            from_currency="USD",
            to_currency="EUR",
            rate=0.90,
            effective_date=date(2026, 5, 1),
        ),
    )
    updated = service.update_exchange_rate(db, r["id"], ExchangeRateUpdate(rate=0.95))
    assert updated["rate"] == 0.95


def test_exchange_rate_duplicate_pair_same_date_raises_conflict(db):
    """Unique constraint on (from, to, date) translated to Conflict."""
    service.create_exchange_rate(
        db,
        ExchangeRateCreate(
            from_currency="USD",
            to_currency="JPY",
            rate=150.0,
            effective_date=date(2026, 5, 1),
        ),
    )
    with pytest.raises(service.Conflict):
        service.create_exchange_rate(
            db,
            ExchangeRateCreate(
                from_currency="USD",
                to_currency="JPY",
                rate=151.0,
                effective_date=date(2026, 5, 1),
            ),
        )


# ─── fetch_exchange_rates: injected httpx client ────────────


def _build_fx_mock_client(rates: dict[str, float], *, result: str = "success") -> httpx.Client:
    """Factory for a `httpx.Client` backed by a MockTransport that returns
    the given rates payload. Lets us exercise the upsert logic without ever
    hitting open.er-api.com."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"result": result, "base_code": "USD", "rates": rates,
                  **({"error-type": "invalid"} if result != "success" else {})},
        )
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_exchange_rates_first_call_creates_rows(db):
    """Fresh DB + 2 targets in payload → created=2, updated=0."""
    client = _build_fx_mock_client({"JPY": 150.0, "EUR": 0.92, "USD": 1.0})
    result = service.fetch_exchange_rates(
        db, base="USD", targets=["JPY", "EUR"], client=client
    )
    assert result.created == 2
    assert result.updated == 0
    assert result.base == "USD"

    # Persisted with source="api"
    rows = service.list_exchange_rates(db, from_currency="USD")
    assert {r["to_currency"] for r in rows} == {"JPY", "EUR"}
    assert all(r["source"] == "api" for r in rows)


def test_fetch_exchange_rates_second_call_updates_existing_rows(db):
    """Re-running fetch on the same day → same rows, but updated_count goes up."""
    client = _build_fx_mock_client({"JPY": 150.0, "EUR": 0.92, "USD": 1.0})
    service.fetch_exchange_rates(db, base="USD", targets=["JPY", "EUR"], client=client)

    client2 = _build_fx_mock_client({"JPY": 151.5, "EUR": 0.93, "USD": 1.0})
    result = service.fetch_exchange_rates(
        db, base="USD", targets=["JPY", "EUR"], client=client2
    )
    assert result.created == 0
    assert result.updated == 2

    # New rate value persisted
    rows = service.list_exchange_rates(db, from_currency="USD", to_currency="JPY")
    assert rows[0]["rate"] == 151.5


def test_fetch_exchange_rates_http_error_raises_upstream_unavailable(db):
    """HTTP 500 from the upstream API → UpstreamUnavailable (mapped to 502)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server exploded")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(service.UpstreamUnavailable):
        service.fetch_exchange_rates(db, base="USD", targets=["JPY"], client=client)


def test_fetch_exchange_rates_api_error_payload_raises_upstream_unavailable(db):
    """HTTP 200 but `result=error` payload → UpstreamUnavailable."""
    client = _build_fx_mock_client({}, result="error")
    with pytest.raises(service.UpstreamUnavailable):
        service.fetch_exchange_rates(db, base="USD", targets=["JPY"], client=client)


def test_fetch_exchange_rates_skips_base_in_targets(db):
    """API includes USD→USD=1.0. Self-rates must be skipped (would dirty the table)."""
    client = _build_fx_mock_client({"USD": 1.0, "JPY": 150.0})
    result = service.fetch_exchange_rates(
        db, base="USD", targets=["USD", "JPY"], client=client
    )
    # Only JPY persisted; USD self-rate filtered.
    assert result.created == 1
    rows = service.list_exchange_rates(db, from_currency="USD")
    assert {r["to_currency"] for r in rows} == {"JPY"}
