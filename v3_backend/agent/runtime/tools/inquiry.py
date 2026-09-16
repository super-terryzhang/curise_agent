"""Inquiry tools — kick off + read state via `domains.inquiry.*`.

Authorization: the orchestrator opens its own DB session for the bg
job and doesn't take user_id. So we must verify order ownership HERE
in the agent tool before kicking off the run — same check the HTTP
`POST /generate-inquiry` endpoint does (`_ensure_order_visible`).
Without this guard, any chat user could generate inquiries for any
order id by passing it to the tool.
"""

from __future__ import annotations

import json

from general_agent import ToolContext, tool

from agent.runtime.deps import get_deps
from domains.inquiry import orchestrator
from domains.inquiry.errors import InquiryError
from domains.orders import service as orders_service
from domains.orders.errors import OrderError


def _ensure_order_owned(deps, order_id: int) -> str | None:
    """Return None if user owns the order, else an error string."""
    is_admin = deps.user_role in ("superadmin", "admin")
    try:
        orders_service.get_order(
            deps.db, order_id=order_id, user_id=deps.user_id, is_admin=is_admin
        )
    except OrderError as exc:
        return f"Error: {exc}"
    return None


@tool(toolset="business", emoji="✉️")
def generate_inquiry(order_id: int, *, ctx: ToolContext) -> str:
    """Start inquiry generation for an order.

    Spawns a background job that produces one Excel inquiry per supplier.
    The frontend SSE stream pushes per-supplier progress. Returns the
    current snapshot of the inquiry state immediately (legacy v2-shaped
    JSON).

    AFTER this call returns successfully (at least one supplier in the
    returned state has a non-null `excel_file_url`), you MUST also call
    `present_artifact(component="inquiry_batch_card", data={"order_id":
    <order_id>}, narration="订单 #<po_number or id> 的 N 份询价单")` so
    the user sees the generated file list in the workspace pane. Skip
    this call only when ALL suppliers failed (no files produced).

    Args:
        order_id: Numeric order id to generate inquiries for.
    """
    deps = get_deps(ctx)
    err = _ensure_order_owned(deps, order_id)
    if err is not None:
        return err
    try:
        state = orchestrator.run_inquiry(order_id, max_workers=4)
    except (InquiryError, OrderError) as exc:
        return f"Error: {exc}"
    return json.dumps(state.to_legacy_dict(), ensure_ascii=False, default=str)


@tool(toolset="business", emoji="🔁")
def regenerate_supplier_inquiry(
    order_id: int,
    supplier_id: int,
    template_id: int = 0,
    *,
    ctx: ToolContext,
) -> str:
    """Re-generate the inquiry Excel for a single supplier.

    Use this for retrying after a transient error or when the user
    swaps to a different template for that supplier.

    AFTER this call returns successfully you MUST re-dispatch
    `present_artifact(component="inquiry_batch_card", data={"order_id":
    <order_id>}, narration="订单 #<po_number or id> 的询价单（已更新）")`
    so the workspace card reflects the new file. The component re-fetches
    the live state, so the new file URL surfaces correctly.

    Args:
        order_id: Numeric order id.
        supplier_id: Numeric supplier id whose inquiry should be regenerated.
        template_id: Optional template id to override the default match.
    """
    deps = get_deps(ctx)
    err = _ensure_order_owned(deps, order_id)
    if err is not None:
        return err
    try:
        state = orchestrator.run_inquiry_for_supplier(
            order_id,
            supplier_id,
            template_id=template_id or None,
        )
    except (InquiryError, OrderError) as exc:
        return f"Error: {exc}"
    return json.dumps(state.to_legacy_dict(), ensure_ascii=False, default=str)


# `get_inquiry_state` was removed in P2 cleanup — `get_order_detail`
# already returns the same `inquiry_data` blob, so a separate tool was
# duplicate surface and made the LLM pick the wrong one. Use
# `get_order_detail(order_id)` and read the `inquiry_data` field.
