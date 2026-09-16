"""Pure-function tests for compute_pnl.

Empty / single-currency / multi-currency / tax-rate variation / unmatched-
product / FX-missing / negative-profit-tax-floor scenarios. No DB.

If the equation strip in the UI shows wrong numbers, these tests are
where the contract is enforced — both UI and Excel exporter read from
`Pnl.to_dict()` so they'll stay in sync.
"""

from __future__ import annotations

from domains.orders.financials.computation import (
    CostItemInput,
    compute_pnl,
)
from domains.orders.financials.currency import FxRate


def test_product_price_currency_is_independent_of_po_currency():
    pnl = compute_pnl(
        match_results=[{"quantity": 2, "unit_price": 10,
                        "matched_product": {"price": 600, "contract_price": 900, "currency": "JPY"}}],
        order_currency="USD", display_currency="USD", cost_items=[],
        rates=[FxRate("JPY", "USD", 0.01)], tax_rate=0,
    )
    data = pnl.to_dict()
    assert data["summary"]["product_revenue"] == 20
    assert data["summary"]["product_cost"] == 12
    assert data["product_lines"][0]["contract_price"] == 9
    assert pnl.warnings == []


def test_missing_historical_product_currency_is_visible_without_rewriting_snapshot():
    matched = {"price": 6, "contract_price": 9}
    pnl = compute_pnl(
        match_results=[{"quantity": 2, "unit_price": 10, "matched_product": matched}],
        order_currency="USD", cost_items=[], rates=[], tax_rate=0,
    )
    assert pnl.to_dict()["summary"]["product_cost"] == 12
    assert any("价格快照缺少币种" in warning for warning in pnl.warnings)
    assert "currency" not in matched


def test_missing_selling_price_exchange_rate_is_not_a_fake_converted_price():
    pnl = compute_pnl(
        match_results=[{"quantity": 1, "unit_price": 10,
                        "matched_product": {"contract_price": 900, "currency": "JPY"}}],
        order_currency="USD", cost_items=[], rates=[], tax_rate=0,
    )
    assert pnl.to_dict()["product_lines"][0]["contract_price"] is None
    assert any("卖价转换失败" in warning for warning in pnl.warnings)


def test_missing_purchase_exchange_rate_excludes_unconverted_cost():
    pnl = compute_pnl(
        match_results=[{"quantity": 2, "unit_price": 10,
                        "matched_product": {"price": 600, "currency": "JPY"}}],
        order_currency="USD", cost_items=[], rates=[], tax_rate=0,
    )
    assert pnl.product_cost == 0
    assert pnl.product_lines[0].unit_cost_display is None
    assert not pnl.product_lines[0].matched
    assert any("成本转换失败" in warning for warning in pnl.warnings)


# ─── Empty cases ────────────────────────────────────────────


def test_empty_order_returns_zeroed_pnl():
    pnl = compute_pnl(
        match_results=None,
        order_currency="JPY",
        cost_items=[],
        rates=[],
        tax_rate=0.06,
    )
    s = pnl.to_dict()["summary"]
    assert s["product_revenue"] == 0
    assert s["gross_profit"] == 0
    assert s["net_profit"] == 0
    assert s["gross_margin"] == 0
    assert pnl.product_lines == []
    assert pnl.cost_items == []
    assert pnl.warnings == []


# ─── Single-currency happy path ────────────────────────────


def test_single_currency_matched_product():
    """Order in JPY, supplier in JPY, no extra costs, no tax.
    Should give clean revenue/cost/profit/margin numbers."""
    pnl = compute_pnl(
        match_results=[
            {
                "product_code": "A1",
                "product_name": "Apple",
                "quantity": 10,
                "unit_price": 100.0,
                "matched_product": {"supplier_id": 1, "price": 60.0},
            }
        ],
        order_currency="JPY",
        cost_items=[],
        rates=[],
        tax_rate=0.0,
    )
    s = pnl.to_dict()["summary"]
    assert s["product_revenue"] == 1000.0
    assert s["product_cost"] == 600.0
    assert s["gross_profit"] == 400.0
    assert s["gross_margin"] == 40.0
    assert s["tax_amount"] == 0
    assert s["net_profit"] == 400.0


def test_unmatched_product_contributes_revenue_only():
    """A product with no matched_product still has revenue but cost=0
    and the line should be flagged matched=False."""
    pnl = compute_pnl(
        match_results=[
            {
                "product_code": "X9",
                "product_name": "Mystery",
                "quantity": 5,
                "unit_price": 50.0,
                # no matched_product
            }
        ],
        order_currency="JPY",
        cost_items=[],
        rates=[],
        tax_rate=0.0,
    )
    line = pnl.product_lines[0]
    assert line.revenue == 250.0 and line.cost == 0.0 and not line.matched
    assert line.margin == 100.0  # 100% margin since cost is 0


# ─── Extra cost items ──────────────────────────────────────


def test_extra_cost_items_subtract_from_gross_profit():
    pnl = compute_pnl(
        match_results=[
            {
                "quantity": 10,
                "unit_price": 100.0,
                "matched_product": {"price": 60.0},
            }
        ],
        order_currency="JPY",
        cost_items=[
            CostItemInput(id=1, category="运费", amount=100.0, currency="JPY"),
            CostItemInput(id=2, category="保险", amount=50.0, currency="JPY"),
        ],
        rates=[],
        tax_rate=0.0,
    )
    s = pnl.to_dict()["summary"]
    assert s["product_revenue"] == 1000.0
    assert s["product_cost"] == 600.0
    assert s["extra_costs_total"] == 150.0
    assert s["total_cost"] == 750.0
    assert s["gross_profit"] == 250.0


# ─── Multi-currency cost items ─────────────────────────────


def test_cost_item_in_different_currency_gets_converted():
    """Order in JPY, freight charged in CNY → freight gets converted
    via the rate table before adding to total_cost."""
    pnl = compute_pnl(
        match_results=[
            {
                "quantity": 10,
                "unit_price": 1000.0,
                "matched_product": {"price": 600.0},
            }
        ],
        order_currency="JPY",
        cost_items=[
            CostItemInput(id=1, category="运费", amount=100.0, currency="CNY"),
        ],
        rates=[FxRate("CNY", "JPY", 21.0)],  # 1 CNY = 21 JPY
        tax_rate=0.0,
    )
    s = pnl.to_dict()["summary"]
    # Revenue = 10*1000 = 10000 JPY
    # Product cost = 10*600 = 6000 JPY
    # Extra cost = 100 CNY * 21 = 2100 JPY
    # Gross profit = 10000 - 6000 - 2100 = 1900 JPY
    assert s["extra_costs_total"] == 2100.0
    assert s["gross_profit"] == 1900.0


def test_cost_item_with_missing_rate_warns_but_continues():
    """Unconvertible cost item → flagged with warning, fx_ok=False,
    and EXCLUDED from extra_costs_total so the rest of the P&L
    stays meaningful."""
    pnl = compute_pnl(
        match_results=[
            {"quantity": 1, "unit_price": 1000.0, "matched_product": {"price": 600.0}}
        ],
        order_currency="JPY",
        cost_items=[
            CostItemInput(id=1, category="exotic", amount=100.0, currency="XOF"),
        ],
        rates=[],  # no XOF rates anywhere
        tax_rate=0.0,
    )
    assert pnl.cost_items[0].fx_ok is False
    assert pnl.extra_costs_total == 0.0  # excluded
    assert any("XOF" in w for w in pnl.warnings)


# ─── Display currency conversion ───────────────────────────


def test_display_currency_converts_everything():
    """Order is in JPY but user wants to see P&L in USD."""
    pnl = compute_pnl(
        match_results=[
            {"quantity": 10, "unit_price": 15000.0, "matched_product": {"price": 9000.0}}
        ],
        order_currency="JPY",
        cost_items=[
            CostItemInput(id=1, category="运费", amount=100.0, currency="USD"),
        ],
        rates=[
            FxRate("JPY", "USD", 1 / 150.0),  # 1 JPY = 1/150 USD
        ],
        tax_rate=0.0,
        display_currency="USD",
    )
    s = pnl.to_dict()["summary"]
    # Revenue = 10 * 15000 JPY = 150,000 JPY = 1000 USD
    # Product cost = 10 * 9000 JPY = 90,000 JPY = 600 USD
    # Extra cost = 100 USD (already in USD)
    # Gross profit = 1000 - 600 - 100 = 300 USD
    assert abs(s["product_revenue"] - 1000.0) < 0.01
    assert abs(s["product_cost"] - 600.0) < 0.01
    assert abs(s["extra_costs_total"] - 100.0) < 0.01
    assert abs(s["gross_profit"] - 300.0) < 0.01


# ─── Tax variations ────────────────────────────────────────


def test_tax_applied_to_gross_profit():
    pnl = compute_pnl(
        match_results=[
            {"quantity": 1, "unit_price": 1000.0, "matched_product": {"price": 0.0}}
        ],
        order_currency="JPY",
        cost_items=[],
        rates=[],
        tax_rate=0.10,
    )
    s = pnl.to_dict()["summary"]
    assert s["gross_profit"] == 1000.0
    assert s["tax_amount"] == 100.0  # 10% of 1000
    assert s["net_profit"] == 900.0


def test_tax_floored_at_zero_when_loss():
    """A loss-making order shouldn't generate a tax credit in this
    simple model. Tax floors at 0 even if gross_profit is negative."""
    pnl = compute_pnl(
        match_results=[
            {"quantity": 1, "unit_price": 100.0, "matched_product": {"price": 500.0}}
        ],
        order_currency="JPY",
        cost_items=[],
        rates=[],
        tax_rate=0.06,
    )
    s = pnl.to_dict()["summary"]
    assert s["gross_profit"] == -400.0
    assert s["tax_amount"] == 0.0  # not -24
    assert s["net_profit"] == -400.0


# ─── Output shape ──────────────────────────────────────────


def test_to_dict_shape_is_stable():
    """Frontend reads exact keys from to_dict(). Locking them so
    silent renames don't break the UI."""
    pnl = compute_pnl(
        match_results=[],
        order_currency="JPY",
        cost_items=[],
        rates=[],
        tax_rate=0.06,
    )
    d = pnl.to_dict()
    assert set(d.keys()) == {
        "display_currency",
        "order_currency",
        "tax_rate",
        "summary",
        "product_lines",
        "cost_items",
        "warnings",
    }
    assert set(d["summary"].keys()) == {
        "product_revenue",
        "product_cost",
        "extra_costs_total",
        "total_cost",
        "gross_profit",
        "gross_margin",
        "tax_amount",
        "net_profit",
        "net_margin",
    }
