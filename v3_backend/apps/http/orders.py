"""Orders HTTP endpoints — `/api/orders/*` compatible with v2 frontend.

Phase 3 ships the 15 core endpoints. The 8 inquiry endpoints live in
`apps/http/inquiry.py` (Phase 5).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from apps.http._deps import CurrentUser, DbDep, Writer
from domains.document import service as document_service
from domains.orders import repository as orders_repo
from domains.orders import service
from domains.orders.errors import BadRequest, NotFound, OrderError, StatusConflict
from domains.orders.models import Order
from domains.orders.schemas import (
    OrderDetail,
    OrderRematchRequest,
    OrderReviewRequest,
    OrderRowResolveRequest,
    OrderUpdateRequest,
)
from infrastructure.db import SessionLocal
from infrastructure.jobs.runner import get_job_runner
from infrastructure.storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orders", tags=["orders"])


def _translate(exc: OrderError) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, StatusConflict):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, BadRequest):
        return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))


def _is_admin(user: CurrentUser) -> bool:
    return user.role in ("superadmin", "admin")


# ═════ Upload ══════════════════════════════════════════════════


@router.post("/upload", response_model=OrderDetail)
async def upload_order(
    db: DbDep,
    user: Writer,
    file: UploadFile = File(...),
) -> OrderDetail:
    """Upload a file and create an Order.

    Under the hood: uploads as Document → pipeline classifies → if PO, the
    projector creates/updates an Order row. Caller gets the Order back once
    the pipeline finishes (SynchronousRunner) or while still processing
    (AsyncioRunner — client polls /api/orders/{id}).
    """
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件名不能为空")
    content = await file.read()
    try:
        document = document_service.upload_document(
            db,
            user_id=user.id,
            filename=file.filename,
            content=content,
            content_type=file.content_type,
        )
    except document_service.DocumentError as exc:
        if isinstance(exc, document_service.BadRequest):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    # If the pipeline already classified + projected (SynchronousRunner),
    # an Order exists. Otherwise we create a stub for the frontend to poll.
    order = orders_repo.get_by_document_id(db, document.id)
    if order is None:
        order = Order(
            user_id=user.id,
            document_id=document.id,
            filename=document.filename,
            file_url=document.file_url,
            file_type=document.file_type,
            status="uploading",
        )
        orders_repo.create(db, order)
    return OrderDetail.model_validate(order)


# ═════ List / Detail ═══════════════════════════════════════════


@router.get("")
def list_orders(
    db: DbDep,
    user: CurrentUser,
    status: str | None = Query(default=None),  # noqa: A002 — v2 frontend sends ?status=...
    fulfillment_status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return service.list_orders(
        db,
        user_id=user.id,
        is_admin=_is_admin(user),
        status=status,
        fulfillment_status=fulfillment_status,
        limit=limit,
        offset=offset,
    )


@router.get("/{order_id}", response_model=OrderDetail)
def get_order(order_id: int, db: DbDep, user: CurrentUser) -> OrderDetail:
    try:
        return service.get_order(db, order_id=order_id, user_id=user.id, is_admin=_is_admin(user))
    except OrderError as exc:
        raise _translate(exc) from exc


@router.delete("/{order_id}")
def delete_order(order_id: int, db: DbDep, user: Writer) -> dict[str, Any]:
    try:
        service.delete_order(db, order_id=order_id, user_id=user.id, is_admin=_is_admin(user))
    except OrderError as exc:
        raise _translate(exc) from exc
    return {"ok": True, "order_id": order_id}


# ═════ Update / Rematch / Reprocess ═══════════════════════════


@router.patch("/{order_id}", response_model=OrderDetail)
def update_order(
    order_id: int,
    body: OrderUpdateRequest,
    db: DbDep,
    user: Writer,
) -> OrderDetail:
    try:
        return service.update_order(
            db, order_id=order_id, user_id=user.id, is_admin=_is_admin(user), body=body
        )
    except OrderError as exc:
        raise _translate(exc) from exc


@router.patch("/{order_id}/products/{row_index}/resolve", response_model=OrderDetail)
def resolve_order_product_row(
    order_id: int,
    row_index: int,
    body: OrderRowResolveRequest,
    db: DbDep,
    user: Writer,
) -> OrderDetail:
    if body.conversion_scope != "order_row" and not _is_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有管理员可以保存可复用的单位换算规则",
        )
    try:
        return service.resolve_order_product_row(
            db,
            order_id=order_id,
            row_index=row_index,
            user_id=user.id,
            is_admin=_is_admin(user),
            body=body,
        )
    except OrderError as exc:
        raise _translate(exc) from exc


@router.post("/{order_id}/rematch", response_model=OrderDetail)
def rematch_order(
    order_id: int,
    body: OrderRematchRequest,
    db: DbDep,
    user: Writer,
) -> OrderDetail:
    try:
        return service.rematch_order(
            db,
            order_id=order_id,
            user_id=user.id,
            is_admin=_is_admin(user),
            country_id=body.country_id,
            port_id=body.port_id,
            delivery_date=body.delivery_date,
        )
    except OrderError as exc:
        raise _translate(exc) from exc


async def _run_reprocess_background(order_id: int, user_id: int, is_admin: bool) -> None:
    """Background job: own DB session, full reprocess. On failure, mark order as error."""
    def _work() -> None:
        db = SessionLocal()
        try:
            service.reprocess_order(
                db, order_id=order_id, user_id=user_id, is_admin=is_admin
            )
        except Exception as exc:
            logger.exception("reprocess background failed for order %d", order_id)
            db.rollback()
            try:
                order = db.query(Order).filter(Order.id == order_id).first()
                if order is not None:
                    order.status = "error"
                    order.processing_error = f"重新处理失败: {exc}"
                    db.commit()
            except Exception:
                logger.exception(
                    "reprocess background: failed to record error on order %d", order_id
                )
                db.rollback()
        finally:
            db.close()

    await asyncio.to_thread(_work)


@router.post("/{order_id}/reprocess", response_model=OrderDetail, status_code=202)
def reprocess_order(order_id: int, db: DbDep, user: Writer) -> OrderDetail:
    """Kick off async reprocess. Returns 202 with status='extracting'; the
    frontend's existing polling on PROCESSING_STATUSES picks up completion."""
    try:
        detail = service.start_reprocess(
            db, order_id=order_id, user_id=user.id, is_admin=_is_admin(user)
        )
    except OrderError as exc:
        raise _translate(exc) from exc

    get_job_runner().submit(
        _run_reprocess_background,
        order_id,
        user.id,
        _is_admin(user),
        job_id=f"reprocess-{order_id}",
    )
    return detail


# ═════ Review / Anomaly / Financial ═══════════════════════════


@router.post("/{order_id}/review")
def review_order(
    order_id: int,
    body: OrderReviewRequest,
    db: DbDep,
    user: Writer,
) -> dict[str, Any]:
    try:
        order = service.mark_reviewed(
            db,
            order_id=order_id,
            user_id=user.id,
            is_admin=_is_admin(user),
            reviewer_id=user.id,
            notes=body.notes,
        )
    except OrderError as exc:
        raise _translate(exc) from exc
    return {"ok": True, "order_id": order.id}


@router.post("/{order_id}/anomaly-check", response_model=OrderDetail)
def anomaly_check(order_id: int, db: DbDep, user: Writer) -> OrderDetail:
    try:
        return service.run_anomaly_check(
            db, order_id=order_id, user_id=user.id, is_admin=_is_admin(user)
        )
    except OrderError as exc:
        raise _translate(exc) from exc


# ═════ Stubs — Phase 4 (template) + future phases ════════════
#
# Inquiry endpoints (8) live in `apps/http/inquiry.py` (Phase 5).


@router.post("/{order_id}/set-template", response_model=OrderDetail)
def set_template_stub(
    order_id: int,  # noqa: ARG001
    db: DbDep,  # noqa: ARG001
    user: Writer,  # noqa: ARG001
) -> OrderDetail:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "模板绑定待 Phase 4 实装")


@router.post("/{order_id}/delivery-environment", response_model=OrderDetail)
def delivery_environment_stub(
    order_id: int,  # noqa: ARG001
    db: DbDep,  # noqa: ARG001
    user: Writer,  # noqa: ARG001
) -> OrderDetail:
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED, "交货环境分析（天气+潮汐）待后续 Phase 实装"
    )


# ═════ File routes ═════════════════════════════════════════════


@router.get("/{order_id}/file-preview")
def file_preview(order_id: int, db: DbDep, user: CurrentUser) -> dict[str, Any]:
    try:
        order_detail = service.get_order(
            db, order_id=order_id, user_id=user.id, is_admin=_is_admin(user)
        )
    except OrderError as exc:
        raise _translate(exc) from exc
    if not order_detail.file_url:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单未关联文件")
    url = get_storage().get_signed_url(order_detail.file_url, expires_in=3600)
    return {"preview_url": url, "file_type": order_detail.file_type}


@router.get("/{order_id}/files")
def list_order_files(order_id: int, db: DbDep, user: CurrentUser) -> list[dict[str, Any]]:
    try:
        order_detail = service.get_order(
            db, order_id=order_id, user_id=user.id, is_admin=_is_admin(user)
        )
    except OrderError as exc:
        raise _translate(exc) from exc
    files: list[dict[str, Any]] = []
    if order_detail.file_url:
        files.append(
            {
                "filename": order_detail.filename,
                "file_url": order_detail.file_url,
                "kind": "original",
            }
        )
    files.extend(order_detail.attachments or [])
    return files


@router.get("/{order_id}/files/{filename}")
def download_order_file(order_id: int, filename: str, db: DbDep, user: CurrentUser) -> Response:
    return _download_file_for_order(db, user, order_id, filename)


@router.post("/{order_id}/files/{filename}/download")
def download_order_file_post(
    order_id: int, filename: str, db: DbDep, user: CurrentUser
) -> Response:
    """POST variant used by the frontend's `downloadOrderFile()` helper.

    Same lookup logic as the GET endpoint — we ship both because the v3 client
    issues a POST (to keep the bearer token out of the browser's URL history)
    while older clients still GET.
    """
    return _download_file_for_order(db, user, order_id, filename)


@router.get("/{order_id}/inquiry-files.zip")
def download_inquiry_files_zip(
    order_id: int, db: DbDep, user: CurrentUser
) -> Response:
    """Stream all inquiry Excel files for an order as a single ZIP.

    Rationale: Chrome (and other browsers) block sites from triggering >1
    automatic download in a row. The previous "全部下载" loop in the frontend
    silently failed on every supplier after the first. Bundling into a single
    ZIP is the standard fix (GitHub releases / S3 / web mail attachments all
    do this) — one user click → one network response → one file save.
    """
    import io
    import posixpath
    import zipfile

    from domains.inquiry import repository as inquiry_repo

    try:
        order_detail = service.get_order(
            db, order_id=order_id, user_id=user.id, is_admin=_is_admin(user)
        )
    except OrderError as exc:
        raise _translate(exc) from exc

    # po_number lives on the Order row, not on the OrderDetail schema — read it
    # directly from the repo so the ZIP filename matches what the customer sees.
    order_row = orders_repo.get(db, order_id)
    po_number = (order_row.po_number if order_row else None) or (
        (order_detail.order_metadata or {}).get("po_number")
    )

    inquiry = inquiry_repo.get_inquiry_by_order(db, order_id)
    if inquiry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单暂无询价单")

    rows = inquiry_repo.list_inquiry_suppliers(db, inquiry.id)
    files: list[tuple[str, bytes]] = []
    storage = get_storage()
    for row in rows:
        if not row.excel_file_url:
            continue
        basename = posixpath.basename(row.excel_file_url)
        try:
            content = storage.download(row.excel_file_url)
        except Exception:
            logger.exception(
                "inquiry zip: failed to fetch %s for order %d", basename, order_id
            )
            continue
        files.append((basename, content))

    if not files:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "暂无可下载的询价单文件")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in files:
            zf.writestr(name, content)
    buf.seek(0)

    po = (po_number or f"order_{order_id}").strip().replace("/", "_")
    zip_name = f"{po}_inquiries.zip"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


def _download_file_for_order(
    db: DbDep, user: CurrentUser, order_id: int, filename: str
) -> Response:
    """Resolve `filename` against an order's downloadable artefacts.

    Two kinds of artefact live under one URL space:
    1. The original uploaded order document (PDF/Excel) — matched by
       `order.filename`.
    2. Inquiry Excel files generated per supplier — matched by the basename
       of `v3_inquiry_suppliers.excel_file_url`. The storage key includes a
       directory prefix (e.g. `inquiries/inquiry_1_2_<hash>.xlsx`); the
       frontend only knows the basename, so we strip the prefix here.

    Any other filename → 404, mirroring v2 behaviour.
    """
    try:
        order_detail = service.get_order(
            db, order_id=order_id, user_id=user.id, is_admin=_is_admin(user)
        )
    except OrderError as exc:
        raise _translate(exc) from exc

    storage = get_storage()

    # Case 1: original uploaded document.
    if order_detail.filename == filename and order_detail.file_url:
        content = storage.download(order_detail.file_url)
        mime = {
            "pdf": "application/pdf",
            "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }.get(order_detail.file_type, "application/octet-stream")
        return StreamingResponse(iter([content]), media_type=mime)

    # Case 2: an inquiry-generated Excel for this order.
    import posixpath

    from domains.inquiry import repository as inquiry_repo

    inquiry = inquiry_repo.get_inquiry_by_order(db, order_id)
    if inquiry is not None:
        rows = inquiry_repo.list_inquiry_suppliers(db, inquiry.id)
        for row in rows:
            if row.excel_file_url and posixpath.basename(row.excel_file_url) == filename:
                content = storage.download(row.excel_file_url)
                return StreamingResponse(
                    iter([content]),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={
                        "Content-Disposition": f'attachment; filename="{filename}"',
                    },
                )

    raise HTTPException(status.HTTP_404_NOT_FOUND, "文件不存在")
