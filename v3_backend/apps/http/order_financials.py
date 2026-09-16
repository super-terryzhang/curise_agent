"""Order financials HTTP — `/api/orders/{id}/financials*` endpoints.

Split out from `apps/http/orders.py` because (a) the file is already
the longest router in the project and (b) financials are an
independent surface that own their own service layer
(`domains/orders/financials/service.py`).

Five endpoints:
  GET    /api/orders/{id}/financials                 full P&L view
  POST   /api/orders/{id}/cost-items                  add a cost item
  PATCH  /api/orders/{id}/cost-items/{item_id}        edit
  DELETE /api/orders/{id}/cost-items/{item_id}        remove
  PATCH  /api/orders/{id}/financial-settings          display_currency + tax_rate

All five use the same auth (`Writer` — read open to all roles,
ownership check inside service) and the same FastAPI router prefix.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response

from fastapi import Depends

from apps.http._deps import DbDep, Writer, require_capability
from domains.orders.errors import BadRequest, NotFound, OrderError
from domains.orders.financials import service
from domains.orders.financials.excel_export import export_pnl_xlsx
from domains.orders.financials.schemas import (
    CostItemCreate,
    CostItemResponse,
    CostItemUpdate,
    FinancialSettingsUpdate,
)
from infrastructure.capabilities import CAP_FINANCIALS_VIEW

logger = logging.getLogger(__name__)

# Same prefix as orders router so the URLs nest naturally
# (`/api/orders/{id}/financials` looks like a sub-resource, which it
# semantically is). Keeping a separate router means we can mount it
# independently of the main orders router in main.py.
#
# Router-level `financials.view` capability gate (2026-06-22): white-list
# access via `v3_user_capabilities`. superadmin auto-passes; everyone
# else gets 403 until a superadmin grants them the cap through the
# user-management UI. Sits on TOP of `Writer` (the per-endpoint role
# check) so a no-role user still gets 401, a no-cap Writer gets 403.
router = APIRouter(
    prefix="/orders",
    tags=["order-financials"],
    dependencies=[Depends(require_capability(CAP_FINANCIALS_VIEW))],
)


def _translate(exc: OrderError) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, BadRequest):
        return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))


def _is_admin(user: Writer) -> bool:
    return user.role in ("superadmin", "admin")


# ─── GET — full computed P&L ────────────────────────────────


@router.get("/{order_id}/financials")
def get_order_financials(
    order_id: int, db: DbDep, user: Writer
) -> dict[str, Any]:
    """Return the full computed P&L for this order, in the order's
    `display_currency` (or PO currency if unset). Computed fresh on
    every call — no caching."""
    try:
        return service.get_financials(
            db, order_id=order_id, user_id=user.id, is_admin=_is_admin(user)
        )
    except OrderError as exc:
        raise _translate(exc) from exc


# ─── Cost item CRUD ─────────────────────────────────────────


@router.post(
    "/{order_id}/cost-items",
    response_model=CostItemResponse,
    status_code=201,
)
def create_cost_item(
    order_id: int, body: CostItemCreate, db: DbDep, user: Writer
) -> CostItemResponse:
    try:
        item = service.create_cost_item(
            db,
            order_id=order_id,
            user_id=user.id,
            is_admin=_is_admin(user),
            body=body,
        )
    except OrderError as exc:
        raise _translate(exc) from exc
    return CostItemResponse.model_validate(item)


@router.patch(
    "/{order_id}/cost-items/{item_id}",
    response_model=CostItemResponse,
)
def update_cost_item(
    order_id: int,
    item_id: int,
    body: CostItemUpdate,
    db: DbDep,
    user: Writer,
) -> CostItemResponse:
    try:
        item = service.update_cost_item(
            db,
            order_id=order_id,
            item_id=item_id,
            user_id=user.id,
            is_admin=_is_admin(user),
            body=body,
        )
    except OrderError as exc:
        raise _translate(exc) from exc
    return CostItemResponse.model_validate(item)


@router.delete("/{order_id}/cost-items/{item_id}", status_code=204)
def delete_cost_item(
    order_id: int, item_id: int, db: DbDep, user: Writer
):
    try:
        service.delete_cost_item(
            db,
            order_id=order_id,
            item_id=item_id,
            user_id=user.id,
            is_admin=_is_admin(user),
        )
    except OrderError as exc:
        raise _translate(exc) from exc
    return Response(status_code=204)


# ─── Excel export ───────────────────────────────────────────


@router.get("/{order_id}/financials/export.xlsx")
def export_financials_xlsx(order_id: int, db: DbDep, user: Writer) -> Response:
    """Download a P&L workbook for this order. Computed live from the
    same `service.get_financials()` payload the UI renders."""
    try:
        pnl = service.get_financials(
            db, order_id=order_id, user_id=user.id, is_admin=_is_admin(user)
        )
    except OrderError as exc:
        raise _translate(exc) from exc
    blob = export_pnl_xlsx(pnl)
    po_number = (pnl.get("meta") or {}).get("po_number") or f"order-{order_id}"
    # HTTP headers are latin-1; RFC 5987 needs percent-encoding for the
    # UTF-8 filename. `filename=` carries the ASCII fallback for old
    # browsers, `filename*=UTF-8''…` carries the Chinese name.
    from urllib.parse import quote
    utf8_name = quote(f"利润报告_{po_number}.xlsx", safe="-_.")
    return Response(
        content=blob,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"order_{order_id}.xlsx\"; "
                f"filename*=UTF-8''{utf8_name}"
            ),
        },
    )


# ─── Per-order settings (display currency + tax rate) ──────


@router.patch("/{order_id}/financial-settings")
def update_financial_settings(
    order_id: int,
    body: FinancialSettingsUpdate,
    db: DbDep,
    user: Writer,
) -> dict[str, Any]:
    """Update display_currency or tax_rate on the order. Returns the
    fresh P&L view so the UI can re-render in one round-trip."""
    try:
        service.update_settings(
            db,
            order_id=order_id,
            user_id=user.id,
            is_admin=_is_admin(user),
            body=body,
        )
        return service.get_financials(
            db, order_id=order_id, user_id=user.id, is_admin=_is_admin(user)
        )
    except OrderError as exc:
        raise _translate(exc) from exc
