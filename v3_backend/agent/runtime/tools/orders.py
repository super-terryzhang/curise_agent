"""Order tools — read + safe writes via service; risky writes via propose.

Tier 1 (direct execute):
- list_orders / get_order_detail (read)
- update_order with metadata-only fields
- rematch_order (re-runs the matching pipeline; reversible)

Tier 2 (propose_action):
- update_order with financial fields (products / invoice_* / payment_*)
- delete_order — soft-delete via service, but always confirm

Why split inside `update_order` rather than two separate tools: the
LLM doesn't reliably pick the right one ("update_order_safe" vs
"update_order_dangerous"). Routing inside the tool by field name gives
us the same outcome with one entry point.
"""

from __future__ import annotations

import json

from general_agent import ToolContext, tool

from agent.runtime.approvals import ActionSpec, register_action
from agent.runtime.deps import get_deps
from domains.orders import service as orders_service
from domains.orders.errors import OrderError
from domains.orders.financials import service as financials_service
from domains.orders.financials.computation import CostItemInput
from domains.orders.schemas import OrderUpdateRequest


@tool(toolset="business", emoji="📋")
def list_orders(status: str = "", limit: int = 20, *, ctx: ToolContext) -> str:
    """List recent orders, newest first.

    WHEN TO USE
      - User asks "我们有多少订单" / "最近的订单" / "今天上传了什么"
      - You need to pick an order_id before drilling into a specific
        order with `get_order_detail` / `list_order_products` /
        `get_order_match_stats`

    NOT FOR (use these instead)
      - Catalog/products → `search_masterdata`
      - Documents that haven't become orders → `list_documents`
      - A specific order's products → `list_order_products`

    Args:
        status: Optional status filter. One of:
          "pending" | "processing" | "ready_for_review" | "failed" |
          "completed". Empty = no filter.
        limit: Page size, 1-50. Default 20.

    RETURNS (≤ 4 KB JSON for 20 rows)
      {total, items: [{
        id, filename, status, po_number, ship_name, product_count,
        has_inquiry, created_at
      }]}
      `total` is the unfiltered count BEFORE pagination so the agent
      can tell users "20 shown of N total".

    TYPICAL TOKEN COST: ~600-1200 tokens for 20 rows.
    """
    deps = get_deps(ctx)
    is_admin = deps.user_role in ("superadmin", "admin")
    bounded_limit = max(1, min(int(limit) if limit else 20, 50))
    try:
        result = orders_service.list_orders(
            deps.db,
            user_id=deps.user_id,
            is_admin=is_admin,
            status=status or None,
            fulfillment_status=None,
            limit=bounded_limit,
            offset=0,
        )
    except OrderError as exc:
        return f"Error: {exc}"
    items = [
        {
            "id": item["id"],
            "filename": item.get("filename"),
            "status": item.get("status"),
            "po_number": (item.get("order_metadata") or {}).get("po_number"),
            "ship_name": (item.get("order_metadata") or {}).get("ship_name"),
            "product_count": item.get("product_count"),
            "has_inquiry": item.get("has_inquiry"),
            "created_at": item.get("created_at"),
        }
        for item in result.get("items", [])
    ]
    return json.dumps(
        {"total": result.get("total"), "items": items},
        ensure_ascii=False,
        default=str,
    )


# Fields the agent may update directly (Tier 1). Mirrors the safe
# subset of `domains.orders.service._UPDATABLE_COLUMNS` — metadata only,
# no financial / line-item changes.
_AGENT_DIRECT_UPDATE_FIELDS = frozenset(
    {
        "po_number",
        "ship_name",
        "vendor_name",
        "delivery_date",
        "order_date",
        "currency",
        "destination_port",
        "country_id",
        "port_id",
        "review_notes",
        "order_metadata",
    }
)


@tool(toolset="business", emoji="🔍")
def get_order_detail(order_id: int, *, ctx: ToolContext) -> str:
    """Lightweight order overview — METADATA ONLY.

    WHEN TO USE
      - User asks "what's order N about?" / "订单 N 是什么"
      - You need PO number, ship name, dates, totals, status
      - Quick disambiguation between similar-looking orders

    NOT FOR (use these instead)
      - Product list / "what's in this order" → `list_order_products`
      - Match quality / unmatched count → `get_order_match_stats`
      - Inquiry status / supplier files → `list_order_files` + `inquiry-readiness` HTTP
      - Raw OCR text → not available in chat tools (use UI)

    RETURNS (~1 KB JSON)
      {id, filename, status, po_number, ship_name, vendor_name,
       currency, order_date, delivery_date, destination_port,
       country_id, port_id, product_count, total_amount,
       fulfillment_status, has_inquiry, has_match_results,
       is_reviewed, created_at}

    TYPICAL TOKEN COST: ~250-350 tokens.

    Note: the heavy fields (`products`, `match_results`, `extraction_data`,
    `inquiry_data`, `anomaly_data`) used to be dumped here too. They were
    removed 2026-05-14 because dumping 50 KB of structured data into the
    agent's context (a) blew past general-agent's 16 KB tool-output cap
    (so the agent only saw a truncated suffix anyway) and (b) wasted
    tokens on every subsequent reasoning step. The narrow tools above
    let the agent ask for exactly the slice it needs.

    Args:
        order_id: Numeric order id.
    """
    deps = get_deps(ctx)
    is_admin = deps.user_role in ("superadmin", "admin")
    try:
        detail = orders_service.get_order(
            deps.db,
            order_id=order_id,
            user_id=deps.user_id,
            is_admin=is_admin,
        )
    except OrderError as exc:
        return f"Error: {exc}"
    # Slim projection. Anything that lives inside a large nested JSON
    # (match_results, products, extraction_data, ...) is intentionally
    # dropped — see the narrow tools listed in this docstring.
    meta = detail.order_metadata or {}
    out = {
        "id": detail.id,
        "filename": detail.filename,
        "status": detail.status,
        "po_number": getattr(detail, "po_number", None) or meta.get("po_number"),
        "ship_name": getattr(detail, "ship_name", None) or meta.get("ship_name"),
        "vendor_name": getattr(detail, "vendor_name", None) or meta.get("vendor_name"),
        "currency": getattr(detail, "currency", None) or meta.get("currency"),
        "order_date": getattr(detail, "order_date", None) or meta.get("order_date"),
        "delivery_date": detail.delivery_date or meta.get("delivery_date"),
        "destination_port": getattr(detail, "destination_port", None)
        or meta.get("destination_port"),
        "country_id": detail.country_id,
        "port_id": detail.port_id,
        "product_count": detail.product_count,
        "total_amount": float(detail.total_amount) if detail.total_amount else None,
        "fulfillment_status": detail.fulfillment_status,
        "has_inquiry": bool(detail.inquiry_data),
        "has_match_results": bool(detail.match_results),
        "is_reviewed": detail.is_reviewed,
        "created_at": str(detail.reviewed_at) if detail.reviewed_at else None,
    }
    return json.dumps(out, ensure_ascii=False, default=str)


@tool(toolset="business", emoji="📦")
def list_order_products(
    order_id: int,
    query: str = "",
    match_status: str = "",
    limit: int = 20,
    offset: int = 0,
    *,
    ctx: ToolContext,
) -> str:
    """List products in one order with pagination + filtering.

    WHEN TO USE
      - User asks about the products in a SPECIFIC order
        ("订单 103 里有什么蔬菜" / "have any beef" / "哪些没匹配")
      - You need to count / filter by category / match status

    NOT FOR (use these instead)
      - Master-catalog browsing → `search_masterdata`
      - Order metadata only → `get_order_detail`
      - Match-rate summary across all rows → `get_order_match_stats`

    Args:
      order_id: Numeric order id.
      query: Substring matched (case-insensitive) against the row's
        product_name AND the matched DB product's English/Japanese
        name AND its category name.

        ⚠ LANGUAGE NOTE: this is dumb substring match — it does NOT
        translate. Categories are typically stored in English
        ("VEGETABLE", "FRUIT", "MEAT"). If the user asks in Chinese
        ("蔬菜") or Japanese ("野菜"), pass the **English** keyword
        ("vegetable" or "veg") instead, or call this tool first with
        an empty `query` to see what category names actually exist
        in the data, then call again with the matching English keyword.
      match_status: Filter to one of "matched" / "not_matched" /
        "possible_match". Empty = all statuses.
      limit: Page size, 1-50. Default 20.
      offset: Skip first N rows of the filtered result.

    RETURNS (≤ 8 KB JSON)
      {order_id, total_in_order, total_matching_filter, returned,
       has_more,
       items: [{
         row_index, product_code, product_name, quantity, unit,
         unit_price, match_status, match_score, matched_code,
         matched_name_en, matched_name_jp, matched_price,
         category_name, supplier_name
       }]}
      `total_in_order` is the un-filtered row count, `total_matching_filter`
      reflects the query + match_status filter. `has_more=True` if
      pagination didn't reach the end.

    TYPICAL TOKEN COST: ~500-2500 tokens for 20 rows.
    """
    deps = get_deps(ctx)
    is_admin = deps.user_role in ("superadmin", "admin")
    try:
        detail = orders_service.get_order(
            deps.db,
            order_id=order_id,
            user_id=deps.user_id,
            is_admin=is_admin,
        )
    except OrderError as exc:
        return f"Error: {exc}"

    raw_rows: list[dict[str, Any]] = detail.match_results or []
    total_in_order = len(raw_rows)

    # Build id→name maps once for FK enrichment. Cheap (~30-100 rows each).
    from domains.masterdata import service as md
    cat_by_id = {c["id"]: c.get("name") for c in md.list_categories(deps.db)}
    sup_by_id = {s["id"]: s.get("name") for s in md.list_suppliers(deps.db)}

    q = (query or "").strip().lower()
    ms = (match_status or "").strip().lower()
    valid_statuses = {"matched", "not_matched", "possible_match"}
    if ms and ms not in valid_statuses:
        return f"Error: invalid match_status '{match_status}'. Valid: {sorted(valid_statuses)}"

    filtered: list[dict[str, Any]] = []
    for idx, r in enumerate(raw_rows):
        if not isinstance(r, dict):
            continue
        matched = r.get("matched_product") or {}
        if not isinstance(matched, dict):
            matched = {}
        if ms and (r.get("match_status") or "").lower() != ms:
            continue
        category_name = cat_by_id.get(matched.get("category_id"))
        supplier_name = sup_by_id.get(matched.get("supplier_id"))
        if q:
            haystack = " ".join(
                str(s or "").lower()
                for s in [
                    r.get("product_name"),
                    matched.get("product_name_en"),
                    matched.get("product_name_jp"),
                    category_name,
                ]
            )
            if q not in haystack:
                continue
        filtered.append(
            {
                "row_index": idx,
                "product_code": r.get("product_code"),
                "product_name": r.get("product_name"),
                "quantity": r.get("quantity"),
                "unit": r.get("unit"),
                "unit_price": r.get("unit_price"),
                "match_status": r.get("match_status"),
                "match_score": r.get("match_score"),
                "matched_code": matched.get("code"),
                "matched_name_en": matched.get("product_name_en"),
                "matched_name_jp": matched.get("product_name_jp"),
                "matched_price": matched.get("price"),
                "category_name": category_name,
                "supplier_name": supplier_name,
            }
        )

    bounded_limit = max(1, min(int(limit) if limit else 20, 50))
    bounded_offset = max(0, int(offset) if offset else 0)
    page = filtered[bounded_offset : bounded_offset + bounded_limit]
    return json.dumps(
        {
            "order_id": order_id,
            "total_in_order": total_in_order,
            "total_matching_filter": len(filtered),
            "returned": len(page),
            "has_more": bounded_offset + len(page) < len(filtered),
            "items": page,
        },
        ensure_ascii=False,
        default=str,
    )


@tool(toolset="business", emoji="📊")
def get_order_match_stats(order_id: int, *, ctx: ToolContext) -> str:
    """Match-quality counts for an order's products — single tiny number-set.

    WHEN TO USE
      - User asks "what's the match rate" / "多少没匹配" / "有多少需要人工"
      - You want to know if it's safe to push to inquiry generation
        without manual review

    NOT FOR (use these instead)
      - Looking AT the unmatched rows → `list_order_products(match_status="not_matched")`
      - Per-product confidence scores → `list_order_products(query=...)`

    RETURNS (~250 B JSON)
      {order_id, total, matched, possible_match, not_matched,
       match_rate_pct, avg_confidence}
      `match_rate_pct` = round((matched + possible_match) / total * 100, 1)
      `avg_confidence` averages `match_score` across all rows with a
       non-null score; null if no scored rows.

    TYPICAL TOKEN COST: ~70 tokens.
    """
    deps = get_deps(ctx)
    is_admin = deps.user_role in ("superadmin", "admin")
    try:
        detail = orders_service.get_order(
            deps.db,
            order_id=order_id,
            user_id=deps.user_id,
            is_admin=is_admin,
        )
    except OrderError as exc:
        return f"Error: {exc}"

    rows: list[dict[str, Any]] = detail.match_results or []
    total = len(rows)
    matched = 0
    possible = 0
    not_matched = 0
    score_sum = 0.0
    score_n = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        st = (r.get("match_status") or "").lower()
        if st == "matched":
            matched += 1
        elif st == "possible_match":
            possible += 1
        elif st == "not_matched":
            not_matched += 1
        score = r.get("match_score")
        if isinstance(score, (int, float)):
            score_sum += float(score)
            score_n += 1
    return json.dumps(
        {
            "order_id": order_id,
            "total": total,
            "matched": matched,
            "possible_match": possible,
            "not_matched": not_matched,
            "match_rate_pct": (
                round((matched + possible) / total * 100, 1) if total else 0.0
            ),
            "avg_confidence": (
                round(score_sum / score_n, 3) if score_n else None
            ),
        },
        ensure_ascii=False,
    )


@tool(toolset="business", emoji="✏️")
def update_order(order_id: int, fields: str, *, ctx: ToolContext) -> str:
    """Update metadata fields on an order.

    For safe metadata (po_number, ship_name, delivery_date, country_id,
    port_id, order_metadata, review_notes, ...) the change applies
    immediately. For destructive / financial fields (products,
    invoice_amount, payment_amount, ...) this tool refuses — use
    `propose_action` with a specialized action instead.

    Args:
        order_id: Numeric order id.
        fields: JSON object with field-name → new-value pairs.
    """
    deps = get_deps(ctx)
    try:
        body_dict = json.loads(fields) if fields else {}
        if not isinstance(body_dict, dict):
            raise ValueError("must be a JSON object")
    except (ValueError, json.JSONDecodeError) as exc:
        return f"Error: invalid `fields` JSON — {exc}"
    if not body_dict:
        return "Error: no fields supplied"

    unknown = [k for k in body_dict if k not in _AGENT_DIRECT_UPDATE_FIELDS]
    if unknown:
        return (
            f"Error: fields {unknown} are not directly updatable by the agent. "
            "Use propose_action with a specialized action for financial / "
            "structural changes."
        )

    is_admin = deps.user_role in ("superadmin", "admin")
    try:
        body = OrderUpdateRequest(**body_dict)
        detail = orders_service.update_order(
            deps.db,
            order_id=order_id,
            user_id=deps.user_id,
            is_admin=is_admin,
            body=body,
        )
    except OrderError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: {type(exc).__name__}: {exc}"
    return json.dumps(detail.model_dump(), ensure_ascii=False, default=str)


@tool(toolset="business", emoji="🔄")
def rematch_order(order_id: int, *, ctx: ToolContext) -> str:
    """Re-run the product-matching pipeline for an order.

    Reversible (the matching can be re-run again), so this is Tier-1
    direct-execute. The order's status flips to `processing` and back
    to `ready_for_review` once matching finishes.

    Args:
        order_id: Numeric order id to re-match.
    """
    deps = get_deps(ctx)
    is_admin = deps.user_role in ("superadmin", "admin")
    try:
        detail = orders_service.rematch_order(
            deps.db,
            order_id=order_id,
            user_id=deps.user_id,
            is_admin=is_admin,
        )
    except OrderError as exc:
        return f"Error: {exc}"
    return json.dumps(detail.model_dump(), ensure_ascii=False, default=str)


# ─── Financials (read-only + what-if) ────────────────────────


@tool(toolset="business", emoji="💰")
def get_order_financials(order_id: int, *, ctx: ToolContext) -> str:
    """Computed P&L for one order in its display currency.

    WHEN TO USE
      - User asks profit / cost / margin questions about one order
        ("订单 N 利润多少" / "毛利率" / "费用明细")
      - You need numbers to answer "should we adjust pricing"

    NOT FOR
      - Changing cost items — that's user-driven UI work, the agent
        only inspects.
      - What-if scenarios — use `what_if_cost_change` so nothing
        persists.

    RETURNS (~1-4 KB JSON)
      {display_currency, order_currency, tax_rate,
       summary: {product_revenue, product_cost, extra_costs_total,
                 total_cost, gross_profit, gross_margin, tax_amount,
                 net_profit, net_margin},
       product_lines: [...], cost_items: [...], warnings: [...],
       meta: {order_id, po_number, ship_name, status, delivery_date}}

    Args:
        order_id: Numeric order id.
    """
    deps = get_deps(ctx)
    is_admin = deps.user_role in ("superadmin", "admin")
    try:
        result = financials_service.get_financials(
            deps.db,
            order_id=order_id,
            user_id=deps.user_id,
            is_admin=is_admin,
        )
    except OrderError as exc:
        return f"Error: {exc}"
    return json.dumps(result, ensure_ascii=False, default=str)


@tool(toolset="business", emoji="🧮")
def what_if_cost_change(
    order_id: int,
    hypothetical_cost_items: str,
    tax_rate: float | None = None,
    display_currency: str = "",
    *,
    ctx: ToolContext,
) -> str:
    """Recompute P&L with hypothetical cost items / tax / currency
    WITHOUT persisting. Use this for "what if we added 500 USD freight"
    or "what if tax was 8%" questions. The order's real DB state is
    unchanged.

    Args:
        order_id: Numeric order id.
        hypothetical_cost_items: JSON-string array of cost items. Each
          item is {category, amount, currency, notes?}. This REPLACES
          (does not merge with) the stored cost items for this calc.
          Pass "[]" to model "what if we had no extra costs".
        tax_rate: Override per-order tax rate (e.g. 0.08 for 8%). Omit
          to keep the stored rate.
        display_currency: Override display currency (e.g. "USD", "JPY").
          Empty = keep the stored display currency.

    RETURNS: same shape as `get_order_financials` but without `meta`,
    since nothing was persisted.
    """
    deps = get_deps(ctx)
    is_admin = deps.user_role in ("superadmin", "admin")

    try:
        raw_items = json.loads(hypothetical_cost_items) if hypothetical_cost_items else []
        if not isinstance(raw_items, list):
            raise ValueError("must be a JSON array")
    except (ValueError, json.JSONDecodeError) as exc:
        return f"Error: invalid `hypothetical_cost_items` JSON — {exc}"

    items: list[CostItemInput] = []
    for idx, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            return f"Error: item[{idx}] must be a JSON object"
        try:
            items.append(
                CostItemInput(
                    id=None,
                    category=str(raw["category"]).strip(),
                    amount=float(raw["amount"]),
                    currency=str(raw["currency"]).strip().upper(),
                    notes=raw.get("notes"),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            return (
                f"Error: item[{idx}] missing/invalid field — {exc}. "
                "Required: category(str), amount(number), currency(str)."
            )

    try:
        result = financials_service.compute_what_if(
            deps.db,
            order_id=order_id,
            user_id=deps.user_id,
            is_admin=is_admin,
            hypothetical_cost_items=items,
            hypothetical_tax_rate=tax_rate,
            hypothetical_display_currency=display_currency or None,
        )
    except OrderError as exc:
        return f"Error: {exc}"
    return json.dumps(result, ensure_ascii=False, default=str)


@tool(toolset="business", emoji="📊")
def analyze_order_financials(order_id: int, *, ctx: ToolContext) -> str:
    """Structured P&L analysis for one order — supplier breakdown,
    Pareto loss/profit concentration, unmatched-SKU cost extrapolation,
    and a data-anomaly flag.

    WHEN TO USE
      - User asks for an order overview ("分析订单 N" / "这单怎么样" /
        "财务分析" / "订单 P&L 概览").
      - The `order-financial-analysis` skill is active and you need the
        structured numbers to fill its 2-paragraph template.

    NOT FOR
      - Simple profit / margin lookup → use `get_order_financials`
        (returns the raw P&L without analytical breakdown).
      - What-if scenarios → use `what_if_cost_change`.
      - Editing cost items or settings — this tool is read-only.

    RETURNS (~3-8 KB JSON)
      {
        meta: {order_id, po_number, ship_name, delivery_date, currency},
        summary: {revenue, product_cost, gross_profit, tax_amount,
                  net_profit, gross_margin_pct, net_margin_pct,
                  tax_rate_pct},
        data_integrity: {
          matched_count, unmatched_count, unmatched_revenue,
          unmatched_by_category: [{category, sku_count, revenue,
                                   median_ratio_used, est_cost, items}],
          est_extra_cost, adjusted_net_profit, adjusted_net_margin,
          extrapolation_methodology
        },
        supplier_breakdown: [{supplier_id, supplier_name, sku_count,
                              revenue, revenue_share_pct, gross_profit,
                              gross_margin_pct, profitable_count,
                              losing_count}],
        loss_concentration: {
          total_loss, losing_sku_count, pareto_top_2_pct,
          pareto_top_5_pct, top_contributors: [...]
        },
        profit_concentration: {
          total_profit, profitable_sku_count, pareto_top_3_pct,
          pareto_top_5_pct, top_contributors: [...]
        },
        data_anomaly: {flag, reasons}
      }

    Args:
        order_id: Numeric order id.
    """
    deps = get_deps(ctx)
    is_admin = deps.user_role in ("superadmin", "admin")
    try:
        result = financials_service.analyze_order(
            deps.db,
            order_id=order_id,
            user_id=deps.user_id,
            is_admin=is_admin,
        )
    except OrderError as exc:
        return f"Error: {exc}"
    return json.dumps(result, ensure_ascii=False, default=str)


# ─── delete_order: register as Tier-2 action ─────────────────


def _dispatch_delete_order(deps, target_id, payload):  # noqa: ANN001
    is_admin = deps.user_role in ("superadmin", "admin")
    orders_service.delete_order(
        deps.db,
        order_id=target_id,
        user_id=deps.user_id,
        is_admin=is_admin,
    )
    return {"deleted_order_id": target_id, "reason": payload.get("reason", "")}


register_action(
    ActionSpec(
        name="delete_order",
        target_kind="order",
        description="Permanently delete an order (cascade-deletes inquiry data).",
        dispatch=_dispatch_delete_order,
    )
)
