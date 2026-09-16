"""HTTP routes for order display grouping (R7).

Thin pass-through to `domains.orders.groups.service`. Translates
service-level exceptions to HTTP status codes (NotFound → 404,
BadRequest → 400). All routes require `Writer` (includes finance per
R3 since finance owns the order workflow).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from apps.http._deps import Admin, DbDep, Writer
from domains.orders.groups import service
from domains.orders.groups.schemas import (
    OrderGroupAssignBody,
    OrderGroupCreate,
    OrderGroupResponse,
    OrderGroupUpdate,
    SupplyArrangementUpdate,
)

router = APIRouter(prefix="/order-groups", tags=["order-groups"])


@router.get("/arrangements")
def list_supply_arrangements(db: DbDep, user: Writer):
    from domains.orders.groups.arrangements import list_arrangements
    return list_arrangements(db, user_id=user.id)


@router.get("/arrangements/{group_id}")
def get_supply_arrangement_workspace(group_id: int, db: DbDep, user: Writer):
    from domains.orders.groups.arrangements import get_arrangement_workspace
    try:
        return get_arrangement_workspace(db, user_id=user.id, group_id=group_id)
    except service.OrderGroupError as exc:
        raise _translate(exc) from exc


@router.patch("/arrangements/{group_id}")
def update_supply_arrangement(
    group_id: int, body: SupplyArrangementUpdate, db: DbDep, user: Writer
):
    from domains.orders.groups.arrangements import update_arrangement

    try:
        return update_arrangement(
            db, user_id=user.id, group_id=group_id, body=body
        )
    except service.OrderGroupError as exc:
        raise _translate(exc) from exc


@router.post("/orders/{order_id}/classify")
def classify_supply_order(order_id: int, db: DbDep, user: Writer):
    from domains.orders.groups.arrangements import classify_order
    try:
        return classify_order(db, user_id=user.id, order_id=order_id)
    except service.OrderGroupError as exc:
        raise _translate(exc) from exc


@router.get("/auto-group/preview")
def preview_auto_groups(db: DbDep, user: Admin):
    from domains.orders.groups.automation import regroup
    return regroup(db)


@router.post("/auto-group")
def auto_group_existing_orders(db: DbDep, user: Admin):
    from domains.orders.groups.automation import regroup
    return regroup(db, apply=True)


def _translate(exc: service.OrderGroupError) -> HTTPException:
    if isinstance(exc, service.NotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("", response_model=list[OrderGroupResponse])
def list_order_groups(db: DbDep, user: Writer) -> list[dict[str, Any]]:
    return service.list_groups(db, user_id=user.id)


@router.post(
    "", response_model=OrderGroupResponse, status_code=status.HTTP_201_CREATED
)
def create_order_group(
    body: OrderGroupCreate, db: DbDep, user: Writer
) -> dict[str, Any]:
    try:
        return service.create_group(db, user_id=user.id, body=body)
    except service.OrderGroupError as exc:
        raise _translate(exc) from exc


@router.get("/{group_id}", response_model=OrderGroupResponse)
def get_order_group(group_id: int, db: DbDep, user: Writer) -> dict[str, Any]:
    try:
        return service.get_group(db, group_id=group_id, user_id=user.id)
    except service.OrderGroupError as exc:
        raise _translate(exc) from exc


@router.patch("/{group_id}", response_model=OrderGroupResponse)
def update_order_group(
    group_id: int, body: OrderGroupUpdate, db: DbDep, user: Writer
) -> dict[str, Any]:
    try:
        return service.update_group(
            db, group_id=group_id, user_id=user.id, body=body
        )
    except service.OrderGroupError as exc:
        raise _translate(exc) from exc


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_order_group(group_id: int, db: DbDep, user: Writer):
    # No `-> None` annotation: FastAPI ≥ 0.111 treats `-> None` as
    # `response_model=type(None)` which clashes with the 204-no-body
    # invariant; leaving the return implicit keeps the response empty.
    try:
        service.delete_group(db, group_id=group_id, user_id=user.id)
    except service.OrderGroupError as exc:
        raise _translate(exc) from exc


@router.post("/{group_id}/orders", response_model=OrderGroupResponse)
def assign_orders_to_group(
    group_id: int, body: OrderGroupAssignBody, db: DbDep, user: Writer
) -> dict[str, Any]:
    try:
        return service.assign_orders(
            db, group_id=group_id, user_id=user.id, body=body
        )
    except service.OrderGroupError as exc:
        raise _translate(exc) from exc


@router.delete(
    "/{group_id}/orders/{order_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_order_from_group(
    group_id: int, order_id: int, db: DbDep, user: Writer
):
    # Same reason as delete_order_group above — no `-> None`.
    try:
        service.remove_order(
            db, group_id=group_id, user_id=user.id, order_id=order_id
        )
    except service.OrderGroupError as exc:
        raise _translate(exc) from exc


@router.post("/{group_id}/generate-inquiry")
def generate_inquiry_for_group(
    group_id: int, db: DbDep, user: Writer
) -> dict[str, Any]:
    """R7 Gap 2 (2026-06-22): launch an inquiry whose product set spans
    every Order in the group. See `run_inquiry_for_group` docstring.

    Returns the anchor order id so the frontend can subscribe to the
    standard per-order inquiry SSE / preview / download endpoints. The
    actual run is submitted to the background runner so this returns
    quickly."""
    # Service-layer ownership check: confirm the group belongs to the
    # caller. This call raises NotFound for cross-user access (404 not
    # 403) so we don't leak existence.
    try:
        service.require_group_inquiry(db, group_id, user.id)
    except service.OrderGroupError as exc:
        raise _translate(exc) from exc

    from apps.jobs.inquiry_jobs import submit_group_inquiry
    from domains.inquiry import orchestrator
    from domains.inquiry.errors import BadRequest as InquiryBadRequest
    from domains.inquiry.errors import NotFound as InquiryNotFound

    # Anchor lookup also tells us if the group has any orders — bail
    # early with a friendly error if not (the orchestrator would too,
    # but doing it here keeps the user-visible code path short).
    anchor_order_id = orchestrator.get_anchor_order_id_for_group(db, group_id)
    if anchor_order_id is None:
        raise HTTPException(status_code=400, detail="分组内还没有订单")

    try:
        inquiry = orchestrator.queue_inquiry_for_group(db, group_id)
        job_id = submit_group_inquiry(group_id=group_id, inquiry_id=inquiry.id)
    except (InquiryBadRequest, InquiryNotFound) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "ok": True,
        "group_id": group_id,
        "anchor_order_id": anchor_order_id,
        "inquiry_id": inquiry.id,
        "version": inquiry.version,
        "job_id": job_id,
        "status": inquiry.status,
    }


@router.get("/{group_id}/inquiries")
def list_group_inquiries(group_id: int, db: DbDep, user: Writer) -> list[dict[str, Any]]:
    """List preserved arrangement inquiry versions, newest first."""
    try:
        service.get_group(db, group_id=group_id, user_id=user.id)
    except service.OrderGroupError as exc:
        raise _translate(exc) from exc

    from domains.inquiry import service as inquiry_service

    return [
        state.model_dump(mode="json")
        for state in inquiry_service.list_group_inquiry_states(db, group_id)
    ]
