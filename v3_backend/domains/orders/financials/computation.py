"""Pure P&L computation — no DB, no network.

Inputs: order's match_results (revenue + product cost in their native
currencies), the list of user-entered cost items (each in its native
currency), today's FX rate snapshot, the per-order tax_rate, and the
display currency the user wants to see everything in.

Output: a structured `Pnl` dataclass with everything the equation
strip, breakdown bar, and per-product table need to render. Plus a
`warnings` list for stuff like "couldn't convert CNY→JPY, rate
missing — skipped this line".

Why pure:
  - The same function powers the GET /financials endpoint, the
    `what_if_cost_change` agent tool, and the Excel exporter. One
    definition of profit, three call sites, zero divergence risk.
  - Testable without any DB / mocks / fixtures.

What we deliberately do NOT do:
  - Persist the result. Recompute on every read. P&L is cheap (≤100
    products + ~10 cost items = µs); caching invites stale-data bugs.
  - Adjust tax for "tax-exempt" categories. v1 treats tax as a flat
    rate × gross profit. If real-world tax math diverges, user
    overrides the rate per order, or we add a per-line tax_exempt
    flag in v2.
  - Allocate cost items per product (e.g. "spread freight pro-rata
    across all items"). Cost items are order-level totals in v1.
    Per-line allocation would be a v2 if customers ask.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from domains.orders.financials.currency import FxRate, convert


@dataclass(frozen=True)
class CostItemInput:
    """A user-entered cost line as it lives in the DB (native currency)."""

    id: int | None  # None for hypothetical/what-if rows
    category: str
    amount: float
    currency: str
    notes: str | None = None


@dataclass(frozen=True)
class ProductPnlLine:
    """Per-product breakdown — one row in the product detail table."""

    product_code: str | None
    product_name: str
    quantity: float
    unit_price_display: float    # revenue side, converted to display ccy
    revenue: float                # qty * unit_price (display ccy)
    unit_cost_display: float | None
    cost: float                   # qty * supplier price (display ccy); 0 if unmatched
    profit: float                 # revenue - cost
    margin: float                 # profit / revenue * 100, 0 if revenue==0
    supplier_id: int | None
    matched: bool                 # had a matched_product with price
    # R2 / Felix 2026-06-06 — our side's contracted selling price (from
    # Product.contract_price via matched_product). NULL when the matched
    # product hasn't been priced in the master data, or no match. The
    # frontend compares this to `unit_price_display` to flag PO typos /
    # contract drift.
    contract_price_display: float | None = None


@dataclass(frozen=True)
class CostItemOut:
    """One row of the cost-items panel, with the converted amount also
    surfaced so the UI doesn't have to call convert() itself."""

    id: int | None
    category: str
    amount_original: float
    currency_original: str
    amount_display: float       # converted to display ccy
    notes: str | None
    fx_ok: bool                 # false if conversion failed (UI shows ⚠️)


@dataclass(frozen=True)
class Pnl:
    """Full computed P&L for one order, in a single display currency."""

    display_currency: str
    order_currency: str               # the order's PO currency, for reference
    tax_rate: float

    # Top equation strip
    product_revenue: float            # Σ qty * unit_price (display ccy)
    product_cost: float               # Σ qty * supplier_price (display ccy)
    extra_costs_total: float          # Σ cost_items.amount_display
    total_cost: float                 # product_cost + extra_costs_total
    gross_profit: float               # product_revenue - total_cost
    gross_margin: float               # gross_profit / product_revenue * 100
    tax_amount: float                 # tax_rate * gross_profit (floored at 0)
    net_profit: float                 # gross_profit - tax_amount
    net_margin: float                 # net_profit / product_revenue * 100

    # Detail
    product_lines: list[ProductPnlLine] = field(default_factory=list)
    cost_items: list[CostItemOut] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form for HTTP / agent-tool returns."""
        return {
            "display_currency": self.display_currency,
            "order_currency": self.order_currency,
            "tax_rate": self.tax_rate,
            "summary": {
                "product_revenue": round(self.product_revenue, 2),
                "product_cost": round(self.product_cost, 2),
                "extra_costs_total": round(self.extra_costs_total, 2),
                "total_cost": round(self.total_cost, 2),
                "gross_profit": round(self.gross_profit, 2),
                "gross_margin": round(self.gross_margin, 2),
                "tax_amount": round(self.tax_amount, 2),
                "net_profit": round(self.net_profit, 2),
                "net_margin": round(self.net_margin, 2),
            },
            "product_lines": [
                {
                    "product_code": p.product_code,
                    "product_name": p.product_name,
                    "quantity": p.quantity,
                    "unit_price": round(p.unit_price_display, 2),
                    "revenue": round(p.revenue, 2),
                    "unit_cost": (
                        round(p.unit_cost_display, 2)
                        if p.unit_cost_display is not None
                        else None
                    ),
                    "cost": round(p.cost, 2),
                    "profit": round(p.profit, 2),
                    "margin": round(p.margin, 2),
                    "supplier_id": p.supplier_id,
                    "matched": p.matched,
                    "contract_price": (
                        round(p.contract_price_display, 2)
                        if p.contract_price_display is not None
                        else None
                    ),
                }
                for p in self.product_lines
            ],
            "cost_items": [
                {
                    "id": c.id,
                    "category": c.category,
                    "amount_original": round(c.amount_original, 2),
                    "currency_original": c.currency_original,
                    "amount_display": round(c.amount_display, 2),
                    "notes": c.notes,
                    "fx_ok": c.fx_ok,
                }
                for c in self.cost_items
            ],
            "warnings": list(self.warnings),
        }


def compute_pnl(
    *,
    match_results: list[dict[str, Any]] | None,
    order_currency: str | None,
    cost_items: list[CostItemInput],
    rates: list[FxRate],
    tax_rate: float,
    display_currency: str | None = None,
) -> Pnl:
    """Compute the full P&L for one order.

    `match_results` is the list of products the matcher attached to the
    order (see `domains/orders/matching/service.py`). Each item has
    `{quantity, unit_price, product_code, product_name,
    matched_product: {supplier_id, price}}`. Unmatched products
    contribute revenue but zero cost (and a warning).

    `cost_items` are user-entered extra expenses (one per cost row).

    `rates` is the FX snapshot — typically `service.load_rates(db)`
    feeds it.

    All monetary outputs are in `display_currency`. Defaults to
    `order_currency` (then 'USD' as a last resort).
    """
    display = (display_currency or order_currency or "USD").upper()
    order_ccy = (order_currency or display).upper()
    warnings: list[str] = []

    # ─── Product lines (revenue + product cost) ───────────────
    product_lines: list[ProductPnlLine] = []
    product_revenue = 0.0
    product_cost = 0.0

    for row in match_results or []:
        qty_raw = row.get("quantity") or 0
        try:
            qty = float(qty_raw)
        except (TypeError, ValueError):
            qty = 0.0
        unit_price_raw = row.get("unit_price") or 0
        try:
            unit_price_native = float(unit_price_raw)
        except (TypeError, ValueError):
            unit_price_native = 0.0

        # Revenue side: PO unit_price is in order_currency
        rev_conv = convert(unit_price_native, order_ccy, display, rates)
        if not rev_conv.ok and unit_price_native:
            warnings.append(
                f"产品 {row.get('product_code') or row.get('product_name', '?')!r} "
                f"收入转换失败：{rev_conv.reason}"
            )
        unit_price_display = rev_conv.amount
        line_revenue = qty * unit_price_display
        product_revenue += line_revenue

        # Product prices use the currency captured with the order's match.
        # Never join today's Product to guess the currency of a historical price.
        matched = row.get("matched_product") or {}
        product_ccy = str(matched.get("currency") or "").strip().upper()
        if not product_ccy:
            product_ccy = order_ccy
            if matched.get("price") is not None or matched.get("contract_price") is not None:
                warnings.append(
                    f"产品 {row.get('product_code') or row.get('product_name', '?')!r} "
                    f"价格快照缺少币种，暂按订单币种 {order_ccy} 计算，请核实"
                )
        supplier_id = matched.get("supplier_id")
        sup_price_raw = matched.get("price")
        unit_cost_display: float | None = None
        line_cost = 0.0
        has_match = False
        if sup_price_raw is not None:
            try:
                unit_cost_native = float(sup_price_raw)
                cost_conv = convert(unit_cost_native, product_ccy, display, rates)
                if not cost_conv.ok and unit_cost_native:
                    warnings.append(
                        f"产品 {row.get('product_code') or '?'} "
                        f"成本转换失败：{cost_conv.reason}"
                    )
                if cost_conv.ok:
                    unit_cost_display = cost_conv.amount
                    line_cost = qty * unit_cost_display
                    has_match = True
                    product_cost += line_cost
            except (TypeError, ValueError):
                pass

        line_profit = line_revenue - line_cost
        line_margin = (line_profit / line_revenue * 100.0) if line_revenue else 0.0

        # Selling-price comparison uses the same captured product currency.
        contract_price_display: float | None = None
        contract_raw = matched.get("contract_price")
        if contract_raw is not None:
            try:
                contract_native = float(contract_raw)
                contract_conv = convert(
                    contract_native, product_ccy, display, rates
                )
                if not contract_conv.ok:
                    warnings.append(
                        f"产品 {row.get('product_code') or '?'} 卖价转换失败：{contract_conv.reason}"
                    )
                else:
                    contract_price_display = contract_conv.amount
            except (TypeError, ValueError):
                pass

        product_lines.append(
            ProductPnlLine(
                product_code=row.get("product_code"),
                product_name=row.get("product_name") or "",
                quantity=qty,
                unit_price_display=unit_price_display,
                revenue=line_revenue,
                unit_cost_display=unit_cost_display,
                cost=line_cost,
                profit=line_profit,
                margin=line_margin,
                supplier_id=supplier_id,
                matched=has_match,
                contract_price_display=contract_price_display,
            )
        )

    # ─── Extra cost items ──────────────────────────────────
    cost_outs: list[CostItemOut] = []
    extra_costs_total = 0.0
    for item in cost_items:
        conv = convert(item.amount, item.currency, display, rates)
        if not conv.ok and item.amount:
            warnings.append(
                f"费用 {item.category!r} 转换失败：{conv.reason}（原币种 {item.currency}）"
            )
        cost_outs.append(
            CostItemOut(
                id=item.id,
                category=item.category,
                amount_original=item.amount,
                currency_original=item.currency.upper(),
                amount_display=conv.amount,
                notes=item.notes,
                fx_ok=conv.ok,
            )
        )
        if conv.ok:
            extra_costs_total += conv.amount

    # ─── Totals ────────────────────────────────────────────
    total_cost = product_cost + extra_costs_total
    gross_profit = product_revenue - total_cost
    gross_margin = (gross_profit / product_revenue * 100.0) if product_revenue else 0.0
    # Tax: floor at 0 — a loss shouldn't generate a tax credit in this view.
    tax_amount = max(0.0, gross_profit * tax_rate)
    net_profit = gross_profit - tax_amount
    net_margin = (net_profit / product_revenue * 100.0) if product_revenue else 0.0

    return Pnl(
        display_currency=display,
        order_currency=order_ccy,
        tax_rate=tax_rate,
        product_revenue=product_revenue,
        product_cost=product_cost,
        extra_costs_total=extra_costs_total,
        total_cost=total_cost,
        gross_profit=gross_profit,
        gross_margin=gross_margin,
        tax_amount=tax_amount,
        net_profit=net_profit,
        net_margin=net_margin,
        product_lines=product_lines,
        cost_items=cost_outs,
        warnings=warnings,
    )
