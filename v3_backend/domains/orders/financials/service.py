"""Service layer for order financials: CRUD on cost items, settings
updates, and the orchestrated "load everything + compute_pnl" entry
point used by the HTTP endpoint, the Excel exporter, and the agent
tools.

Authorization mirrors the rest of `domains/orders/service.py`:
non-admin users can only touch orders they own. Admins can touch any.

Currency rate loading: each call grabs the latest rates from
`v2_exchange_rates`. The query reduces to "for each (from, to) pair,
the row with max effective_date". For the live FX table (≤1k rows)
this is fine in one round-trip.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from domains.identity import service as identity_service
from domains.masterdata import service as masterdata_service
from domains.orders.errors import NotFound
from domains.orders.financials.computation import (
    CostItemInput,
    compute_pnl,
)
from domains.orders.financials.currency import FxRate
from domains.orders.financials.models import OrderCostItem
from domains.orders.financials.schemas import (
    CostItemCreate,
    CostItemUpdate,
    FinancialSettingsUpdate,
)
from domains.orders.models import Order
from infrastructure.capabilities import CAP_FINANCIALS_VIEW

# ─── Helpers ──────────────────────────────────────────────────


def _load_owned_order(
    db: Session, order_id: int, user_id: int, is_admin: bool
) -> Order:
    if not identity_service.has_capability(db, user_id, CAP_FINANCIALS_VIEW):
        raise NotFound("财务数据不可访问")
    current_user = identity_service.get_business_user(db, user_id)
    is_admin = current_user.role in ("superadmin", "admin")
    order = db.get(Order, order_id)
    if order is None:
        raise NotFound("订单不存在")
    if not is_admin and order.user_id != user_id:
        raise NotFound("订单不存在")  # NotFound rather than Forbidden to avoid id-probing
    return order


def load_rates(db: Session) -> list[FxRate]:
    """Latest rate per (from_currency, to_currency) pair from
    `v2_exchange_rates`. Cheap enough to call per-request (≤1k rows
    typical; daily-refreshed by the FX cron job).

    Routed through `masterdata.service` so this module stays inside
    cross-domain rules (RULE-3 in `scripts/check_arch.py`).
    """
    rows = masterdata_service.list_exchange_rates(db)
    # `list_exchange_rates` returns serialized dicts; reduce duplicates
    # by keeping the row with the most recent effective_date per pair.
    rows_sorted = sorted(
        rows,
        key=lambda r: (
            r["from_currency"],
            r["to_currency"],
            r.get("effective_date") or "",
        ),
    )
    latest: dict[tuple[str, str], dict] = {}
    for r in rows_sorted:
        latest[(r["from_currency"], r["to_currency"])] = r
    return [
        FxRate(
            from_currency=r["from_currency"],
            to_currency=r["to_currency"],
            rate=float(r["rate"]),
        )
        for r in latest.values()
    ]


def _to_cost_input(row: OrderCostItem) -> CostItemInput:
    return CostItemInput(
        id=row.id,
        category=row.category,
        amount=float(row.amount),
        currency=row.currency,
        notes=row.notes,
    )


# ─── Cost item CRUD ──────────────────────────────────────────


def list_cost_items(
    db: Session, *, order_id: int, user_id: int, is_admin: bool
) -> list[OrderCostItem]:
    _load_owned_order(db, order_id, user_id, is_admin)
    return list(
        db.execute(
            select(OrderCostItem)
            .where(OrderCostItem.order_id == order_id)
            .order_by(OrderCostItem.created_at, OrderCostItem.id)
        ).scalars()
    )


def create_cost_item(
    db: Session,
    *,
    order_id: int,
    user_id: int,
    is_admin: bool,
    body: CostItemCreate,
) -> OrderCostItem:
    _load_owned_order(db, order_id, user_id, is_admin)
    item = OrderCostItem(
        order_id=order_id,
        category=body.category.strip(),
        amount=body.amount,
        currency=body.currency.strip().upper(),
        notes=body.notes,
        created_by_user_id=user_id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def update_cost_item(
    db: Session,
    *,
    order_id: int,
    item_id: int,
    user_id: int,
    is_admin: bool,
    body: CostItemUpdate,
) -> OrderCostItem:
    _load_owned_order(db, order_id, user_id, is_admin)
    item = db.get(OrderCostItem, item_id)
    if item is None or item.order_id != order_id:
        raise NotFound("费用项不存在")
    data = body.model_dump(exclude_unset=True)
    if "category" in data and data["category"]:
        item.category = data["category"].strip()
    if "amount" in data and data["amount"] is not None:
        item.amount = data["amount"]
    if "currency" in data and data["currency"]:
        item.currency = data["currency"].strip().upper()
    if "notes" in data:
        item.notes = data["notes"]
    db.commit()
    db.refresh(item)
    return item


def delete_cost_item(
    db: Session,
    *,
    order_id: int,
    item_id: int,
    user_id: int,
    is_admin: bool,
) -> None:
    _load_owned_order(db, order_id, user_id, is_admin)
    item = db.get(OrderCostItem, item_id)
    if item is None or item.order_id != order_id:
        raise NotFound("费用项不存在")
    db.delete(item)
    db.commit()


def update_settings(
    db: Session,
    *,
    order_id: int,
    user_id: int,
    is_admin: bool,
    body: FinancialSettingsUpdate,
) -> Order:
    order = _load_owned_order(db, order_id, user_id, is_admin)
    data = body.model_dump(exclude_unset=True)
    if "display_currency" in data and data["display_currency"]:
        order.display_currency = data["display_currency"].strip().upper()
    if "tax_rate" in data and data["tax_rate"] is not None:
        order.tax_rate = data["tax_rate"]
    db.commit()
    db.refresh(order)
    return order


# ─── Composed: load + compute_pnl ────────────────────────────


_DEFAULT_TAX_RATE = Decimal("0.06")


def get_financials(
    db: Session, *, order_id: int, user_id: int, is_admin: bool
) -> dict[str, Any]:
    """Single entry point used by the HTTP GET endpoint and the agent
    `get_order_financials` tool. Always computes fresh — no caching.

    Returns the JSON-serializable shape from `Pnl.to_dict()` plus a
    `meta` block with order-level context the UI needs (PO number,
    ship name, processor, etc.) but that's not part of the math.
    """
    order = _load_owned_order(db, order_id, user_id, is_admin)
    cost_rows = list_cost_items(
        db, order_id=order_id, user_id=user_id, is_admin=is_admin
    )
    rates = load_rates(db)

    tax_rate_raw = order.tax_rate if order.tax_rate is not None else _DEFAULT_TAX_RATE

    pnl = compute_pnl(
        match_results=order.match_results,
        order_currency=order.currency,
        cost_items=[_to_cost_input(r) for r in cost_rows],
        rates=rates,
        tax_rate=float(tax_rate_raw),
        display_currency=order.display_currency,
    )
    out = pnl.to_dict()
    out["meta"] = {
        "order_id": order.id,
        "po_number": order.po_number,
        "ship_name": order.ship_name,
        "status": order.status,
        "delivery_date": order.delivery_date,
    }
    return out


def compute_what_if(
    db: Session,
    *,
    order_id: int,
    user_id: int,
    is_admin: bool,
    hypothetical_cost_items: list[CostItemInput],
    hypothetical_tax_rate: float | None = None,
    hypothetical_display_currency: str | None = None,
) -> dict[str, Any]:
    """What-if analysis — agent's tool calls this. The hypothetical
    inputs REPLACE the stored ones for this single computation.
    NOTHING gets persisted. Order + rates load from DB so the
    comparison baseline is real, only the overrides are hypothetical.
    """
    order = _load_owned_order(db, order_id, user_id, is_admin)
    rates = load_rates(db)
    tax_rate = (
        hypothetical_tax_rate
        if hypothetical_tax_rate is not None
        else float(order.tax_rate if order.tax_rate is not None else _DEFAULT_TAX_RATE)
    )
    display = hypothetical_display_currency or order.display_currency
    pnl = compute_pnl(
        match_results=order.match_results,
        order_currency=order.currency,
        cost_items=hypothetical_cost_items,
        rates=rates,
        tax_rate=tax_rate,
        display_currency=display,
    )
    return pnl.to_dict()


# ─── Structured analysis (powers the order-financial-analysis skill) ──
#
# `get_financials` returns the raw P&L for the UI. For the agent-side
# `analyze_order_financials` tool we want a richer structured payload
# that surfaces the angles a 2-paragraph brief needs: data integrity
# (unmatched extrapolation), supplier breakdown, Pareto loss / profit
# concentration, and a data-anomaly flag. All numbers are computed here
# so the LLM only renders — never calculates.


_CATEGORY_KEYWORDS = [
    "LETTUCE", "CARROT", "CELERY", "TOMATO", "PEPPER", "CABBAGE",
    "SQUASH", "MELON", "WATERMELON", "APPLE", "ORANGE", "GRAPE",
    "MUSHROOM", "BEAN", "MANGO", "KIWI", "EGGPLANT", "PINEAPPLE",
    "PEAS", "BROCCOLI", "POTATO", "GRAPEFRUIT", "ONION", "CHEESE",
    "BEEF", "CHICKEN", "PORK", "FISH", "SALMON", "SHRIMP", "BUTTER",
    "EGG", "YOGURT", "MILK", "CREAM", "BREAD", "FLOUR", "SUGAR", "OIL",
    "WINE", "JUICE", "BERRY", "HERB", "SPICE", "LEMON", "LIME",
    "GARLIC", "GINGER", "AVOCADO", "ASPARAGUS", "CUCUMBER",
    "BANANA", "PEAR", "PEACH", "PLUM", "WATERMELON",
]


def _categorize(name: str) -> str:
    """Bucket a product name into a coarse category keyword for
    same-category cost extrapolation. Returns "OTHER" when no keyword
    matches."""
    n = (name or "").upper()
    for kw in _CATEGORY_KEYWORDS:
        if kw in n:
            # WATERMELON before MELON is intentional — first match wins.
            return "MELON" if kw == "WATERMELON" else kw
    return "OTHER"


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    return s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2


def analyze_order(
    db: Session, *, order_id: int, user_id: int, is_admin: bool
) -> dict[str, Any]:
    """Deeper structured analysis on top of `get_financials`. Returns
    a dict the agent renders into a 2-paragraph brief.

    Shape:
      {
        meta: {order_id, po_number, ship_name, delivery_date, currency},
        summary: {revenue, product_cost, gross_profit, tax, net_profit,
                  gross_margin, net_margin, displayed_net_margin},
        data_integrity: {
          matched_count, unmatched_count, unmatched_revenue,
          unmatched_by_category: [{category, sku_count, revenue}],
          adjusted_net_profit, adjusted_net_margin,   # by category-median extrapolation
          extrapolation_methodology
        },
        supplier_breakdown: [{
          supplier_id, supplier_name, sku_count, revenue,
          revenue_share_pct, gross_profit, gross_margin_pct,
          profitable_count, losing_count
        }],
        loss_concentration: {
          total_loss, losing_sku_count,
          top_contributors: [{name, supplier_name, qty, profit, share_pct, cum_pct}],
          pareto_top_2_pct, pareto_top_5_pct
        },
        profit_concentration: {
          total_profit, profitable_sku_count,
          top_contributors: [{name, supplier_name, qty, profit, share_pct, cum_pct}],
          pareto_top_3_pct, pareto_top_5_pct
        },
        data_anomaly: {
          flag: bool,
          reasons: [str],   # ratios > 5, all-negative, etc.
        }
      }
    """
    order = _load_owned_order(db, order_id, user_id, is_admin)

    # Reuse get_financials for the canonical summary numbers.
    fin = get_financials(db, order_id=order_id, user_id=user_id, is_admin=is_admin)
    summary = fin["summary"]

    mr = order.match_results or []
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for r in mr:
        if not isinstance(r, dict):
            continue
        mp = r.get("matched_product") or {}
        if isinstance(mp, dict) and mp.get("supplier_id"):
            matched.append(r)
        else:
            unmatched.append(r)

    # Supplier name lookup — via masterdata.service (RULE-3 compliant).
    sup_records = masterdata_service.list_suppliers(db)
    sup_by_id: dict[int, str] = {
        int(s["id"]): str(s.get("name") or f"supplier-{s['id']}")
        for s in sup_records
        if isinstance(s, dict) and "id" in s
    }

    # ─── Per-line numerics: revenue, cost, profit, ratio ────
    enriched: list[dict[str, Any]] = []
    for r in matched:
        mp = r.get("matched_product") or {}
        qty = float(r.get("quantity") or 0)
        up = float(r.get("unit_price") or 0)
        sp = float(mp.get("price") or 0)
        rev = qty * up
        cost = qty * sp
        profit = rev - cost
        ratio = (sp / up) if up > 0 else 0.0
        sid = int(mp.get("supplier_id"))
        enriched.append({
            "name": r.get("product_name") or "",
            "code": r.get("product_code") or "",
            "qty": qty,
            "unit_price": up,
            "supplier_price": sp,
            "revenue": rev,
            "cost": cost,
            "profit": profit,
            "cost_price_ratio": ratio,
            "supplier_id": sid,
            "supplier_name": sup_by_id.get(sid, f"supplier-{sid}"),
        })

    # ─── Supplier breakdown ──────────────────────────────────
    by_sup: dict[int, list[dict[str, Any]]] = {}
    for row in enriched:
        by_sup.setdefault(row["supplier_id"], []).append(row)

    total_matched_rev = sum(r["revenue"] for r in enriched)
    supplier_breakdown = []
    for sid, rows in sorted(by_sup.items(), key=lambda kv: -sum(r["revenue"] for r in kv[1])):
        rev = sum(r["revenue"] for r in rows)
        cost = sum(r["cost"] for r in rows)
        wins = [r for r in rows if r["profit"] > 0]
        losses = [r for r in rows if r["profit"] < 0]
        supplier_breakdown.append({
            "supplier_id": sid,
            "supplier_name": rows[0]["supplier_name"],
            "sku_count": len(rows),
            "revenue": round(rev, 2),
            "revenue_share_pct": round(rev / total_matched_rev * 100, 1) if total_matched_rev > 0 else 0.0,
            "gross_profit": round(rev - cost, 2),
            "gross_margin_pct": round((rev - cost) / rev * 100, 2) if rev > 0 else 0.0,
            "profitable_count": len(wins),
            "losing_count": len(losses),
        })

    # ─── Loss concentration (Pareto) ─────────────────────────
    loss_rows = sorted([r for r in enriched if r["profit"] < 0], key=lambda x: x["profit"])
    total_loss = sum(r["profit"] for r in loss_rows)  # negative number
    loss_contribs = []
    cum = 0.0
    for r in loss_rows[:8]:
        cum += r["profit"]
        share = (r["profit"] / total_loss * 100) if total_loss != 0 else 0.0
        cum_pct = (cum / total_loss * 100) if total_loss != 0 else 0.0
        loss_contribs.append({
            "name": r["name"],
            "code": r["code"],
            "supplier_name": r["supplier_name"],
            "qty": r["qty"],
            "profit": round(r["profit"], 2),
            "share_pct": round(share, 1),
            "cum_pct": round(cum_pct, 1),
        })

    def _cum_at(n: int) -> float:
        if not loss_rows or total_loss == 0:
            return 0.0
        s = sum(r["profit"] for r in loss_rows[:n])
        return round(s / total_loss * 100, 1)

    loss_concentration = {
        "total_loss": round(total_loss, 2),
        "losing_sku_count": len(loss_rows),
        "top_contributors": loss_contribs,
        "pareto_top_2_pct": _cum_at(2),
        "pareto_top_5_pct": _cum_at(5),
    }

    # ─── Profit concentration (Pareto) ───────────────────────
    profit_rows = sorted([r for r in enriched if r["profit"] > 0], key=lambda x: -x["profit"])
    total_profit = sum(r["profit"] for r in profit_rows)
    profit_contribs = []
    cum = 0.0
    for r in profit_rows[:8]:
        cum += r["profit"]
        share = (r["profit"] / total_profit * 100) if total_profit > 0 else 0.0
        cum_pct = (cum / total_profit * 100) if total_profit > 0 else 0.0
        profit_contribs.append({
            "name": r["name"],
            "code": r["code"],
            "supplier_name": r["supplier_name"],
            "qty": r["qty"],
            "profit": round(r["profit"], 2),
            "share_pct": round(share, 1),
            "cum_pct": round(cum_pct, 1),
        })

    def _profit_cum_at(n: int) -> float:
        if not profit_rows or total_profit == 0:
            return 0.0
        s = sum(r["profit"] for r in profit_rows[:n])
        return round(s / total_profit * 100, 1)

    profit_concentration = {
        "total_profit": round(total_profit, 2),
        "profitable_sku_count": len(profit_rows),
        "top_contributors": profit_contribs,
        "pareto_top_3_pct": _profit_cum_at(3),
        "pareto_top_5_pct": _profit_cum_at(5),
    }

    # ─── Data integrity: unmatched SKUs + extrapolation ──────
    # For each unmatched SKU, find same-category matched SKUs and use
    # their MEDIAN cost/price ratio as the cost estimate. Fall back to
    # overall median if no same-category data exists.
    matched_ratios = [r["cost_price_ratio"] for r in enriched if r["cost_price_ratio"] > 0]
    overall_median = _median(matched_ratios) if matched_ratios else 0.0
    ratios_by_cat: dict[str, list[float]] = {}
    for r in enriched:
        if r["cost_price_ratio"] > 0:
            ratios_by_cat.setdefault(_categorize(r["name"]), []).append(r["cost_price_ratio"])

    unmatched_by_cat: dict[str, list[dict[str, Any]]] = {}
    for r in unmatched:
        qty = float(r.get("quantity") or 0)
        up = float(r.get("unit_price") or 0)
        rev = qty * up
        cat = _categorize(r.get("product_name") or "")
        unmatched_by_cat.setdefault(cat, []).append({
            "name": r.get("product_name") or "",
            "qty": qty,
            "revenue": rev,
        })

    total_est_extra_cost = 0.0
    unmatched_breakdown = []
    extrapolation_notes: list[str] = []
    for cat, items in unmatched_by_cat.items():
        cat_rev = sum(x["revenue"] for x in items)
        same_cat_ratios = ratios_by_cat.get(cat, [])
        if same_cat_ratios:
            med = _median(same_cat_ratios)
            note = f"{cat}: 同品类 {len(same_cat_ratios)} 个匹配 SKU，中位成本率 {med:.2f}"
        else:
            med = overall_median
            note = f"{cat}: 无同品类参照，使用整体中位成本率 {med:.2f}"
        est_cost = cat_rev * med
        total_est_extra_cost += est_cost
        unmatched_breakdown.append({
            "category": cat,
            "sku_count": len(items),
            "revenue": round(cat_rev, 2),
            "median_ratio_used": round(med, 3),
            "est_cost": round(est_cost, 2),
            "items": [{"name": x["name"], "qty": x["qty"], "revenue": round(x["revenue"], 2)} for x in items],
        })
        extrapolation_notes.append(note)

    # Re-run net profit with the estimated extra cost.
    displayed_net = summary.get("net_profit", 0)
    displayed_net_margin = summary.get("net_margin", 0)
    product_revenue = summary.get("product_revenue", 0)
    tax_rate = fin.get("tax_rate", 0)
    if unmatched:
        new_gross = product_revenue - (summary.get("product_cost", 0) + total_est_extra_cost)
        new_tax = max(0.0, new_gross * tax_rate)
        adjusted_net = new_gross - new_tax
        adjusted_margin = (adjusted_net / product_revenue * 100) if product_revenue > 0 else 0.0
    else:
        adjusted_net = displayed_net
        adjusted_margin = displayed_net_margin

    data_integrity = {
        "matched_count": len(matched),
        "unmatched_count": len(unmatched),
        "unmatched_revenue": round(sum(float(r.get("quantity") or 0) * float(r.get("unit_price") or 0) for r in unmatched), 2),
        "unmatched_by_category": unmatched_breakdown,
        "est_extra_cost": round(total_est_extra_cost, 2),
        "adjusted_net_profit": round(adjusted_net, 2),
        "adjusted_net_margin": round(adjusted_margin, 2),
        "extrapolation_methodology": (
            "按已匹配 SKU 中同品类的成本占客户报价比例中位数逐项外推；"
            "若无同品类匹配产品，使用所有已匹配 SKU 的整体中位比例。"
        ),
        "extrapolation_per_category": extrapolation_notes,
    }

    # ─── Data anomaly detector ───────────────────────────────
    # Flag the case where the raw numbers don't look like a normal
    # distribution business — e.g. supplier costs systematically much
    # larger than customer prices (suggesting unit-mismatch or stale
    # master data). The skill uses this to switch to a "data quality"
    # framing instead of a normal analysis.
    anomaly_reasons: list[str] = []
    if enriched:
        ratios = [r["cost_price_ratio"] for r in enriched if r["cost_price_ratio"] > 0]
        if ratios:
            extreme_high = [r for r in ratios if r > 5.0]
            if extreme_high:
                anomaly_reasons.append(
                    f"{len(extreme_high)} 个已匹配 SKU 的供应商成本 > 客户报价 5 倍（最高 {max(ratios):.1f} 倍），与单位换算或主数据错误的特征一致"
                )
        net_margin = summary.get("net_margin", 0)
        if net_margin < -20:
            anomaly_reasons.append(
                f"净利率 {net_margin:.1f}% 远低于分销业务正常区间"
            )
    data_anomaly = {"flag": bool(anomaly_reasons), "reasons": anomaly_reasons}

    return {
        "meta": {
            "order_id": order.id,
            "po_number": order.po_number,
            "ship_name": order.ship_name,
            "delivery_date": str(order.delivery_date) if order.delivery_date else None,
            "currency": fin.get("display_currency"),
        },
        "summary": {
            "revenue": round(product_revenue, 2),
            "product_cost": round(summary.get("product_cost", 0), 2),
            "gross_profit": round(summary.get("gross_profit", 0), 2),
            "tax_amount": round(summary.get("tax_amount", 0), 2),
            "net_profit": round(displayed_net, 2),
            "gross_margin_pct": round(summary.get("gross_margin", 0), 2),
            "net_margin_pct": round(displayed_net_margin, 2),
            "tax_rate_pct": round(tax_rate * 100, 2),
        },
        "data_integrity": data_integrity,
        "supplier_breakdown": supplier_breakdown,
        "loss_concentration": loss_concentration,
        "profit_concentration": profit_concentration,
        "data_anomaly": data_anomaly,
    }


__all__ = [
    "load_rates",
    "list_cost_items",
    "create_cost_item",
    "update_cost_item",
    "delete_cost_item",
    "update_settings",
    "get_financials",
    "compute_what_if",
    "analyze_order",
]


def __getattr__(name: str) -> Any:  # pragma: no cover — silences arch-check noise
    raise AttributeError(name)
