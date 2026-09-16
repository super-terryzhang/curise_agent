"""Data upload HTTP — `/api/data-upload/*`.

Endpoints:
  POST /upload     accept an Excel and create a staging batch
  GET  /template   download the canonical product upload template

Why not let the agent handle the upload itself: agents in chat can't
receive raw bytes — they only see text. So the front-end uploads to
this endpoint first, gets back a batch_id, and includes that in the
chat message ("处理 batch_id=42") for the agent to act on via
`parse_uploaded_file` / `preview_upload` / `commit_upload`.

The template endpoint exists so the agent can hand the user a ready-
to-fill .xlsx the moment the master-data-upload skill activates —
removing the "what columns should I have?" guessing game that caused
half of historical upload failures.
"""

from __future__ import annotations

import logging
import hashlib
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from apps.http._deps import DbDep, ProductUploader, Writer
from domains.masterdata.upload import (
    cancel_batch,
    commit_validated_batch,
    get_workflow_batch,
    get_workflow_file_key,
    get_workflow_rows,
    list_workflow_batches,
    parse_excel,
    rollback_batch,
    validate_workflow_batch,
)
from domains.masterdata.upload.contracts import (
    CancelResult,
    CommitResult,
    WorkflowBatch,
    WorkflowBatchPage,
    WorkflowRowsPage,
)
from domains.masterdata.upload.errors import (
    BatchInWrongState,
    BatchNotFound,
    BatchOwnedByOther,
    BatchValidationFailed,
    ParseError,
)
from infrastructure.config import settings
from infrastructure.storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/data-upload", tags=["data-upload"])

# Static template lives in `v3_backend/static/templates/`. Regenerate with
# `python scripts/generate_product_upload_template.py` whenever the
# parser's `_HEADER_ALIASES` change — the test_upload_template suite
# fails CI if you forget.
_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "static"
    / "templates"
    / "product_upload_template.xlsx"
)
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _workflow_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (BatchNotFound, BatchOwnedByOther)):
        return HTTPException(status.HTTP_404_NOT_FOUND, "上传批次不存在")
    if isinstance(exc, (BatchInWrongState, BatchValidationFailed)):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "上传流程处理失败")


@router.post("/upload")
async def upload_data_file(
    db: DbDep,
    user: Writer,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Parse an uploaded Excel into a staging batch.

    The response carries `batch_id` and parse stats. The user (or the
    agent on their behalf) then calls `preview_upload` / `commit_upload`
    referencing that batch_id.
    """
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少文件名")

    blob = await file.read()
    if not blob:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件为空")
    if len(blob) > settings.MAX_UPLOAD_SIZE:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"文件超过 {settings.MAX_UPLOAD_SIZE // (1024 * 1024)}MB",
        )

    try:
        batch = parse_excel(
            db,
            file_bytes=blob,
            filename=file.filename,
            user_id=user.id,
        )
    except ParseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"解析失败：{exc}") from exc
    except Exception as exc:
        logger.exception("upload parse crashed")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, f"上传处理失败：{exc}"
        ) from exc

    return {
        "batch_id": batch.id,
        "filename": batch.filename,
        "status": batch.status,
        "total_rows": batch.total_rows,
    }


@router.get("/template")
def download_template() -> FileResponse:
    """Download the canonical product-upload Excel template — PUBLIC.

    No auth on purpose. The agent puts a markdown link to this URL in
    its reply; the chat UI renders that link as an `<a href>`, and when
    the user clicks it the browser issues a plain `GET` with NO
    Authorization header (the JWT lives only in the SPA's JS, not in
    cookies). Requiring auth here would 401 every click, which is
    exactly the failure mode reported on 2026-05-14 ("点击下载模板 →
    Not authenticated").

    This is fine: the file contains no user data — just the 5 canonical
    column names + three public example rows ("Apple Red Delicious"
    etc.) + an instructions sheet. Shopify / Stripe / Notion all serve
    their import templates publicly for the same reason.

    If you ever need to revisit the security model (e.g. ship a private
    template with seeded supplier names), the right pattern is signed
    URLs minted by an authenticated `/template-url` endpoint, NOT a
    cookie-based gate that breaks markdown links.
    """
    if not _TEMPLATE_PATH.exists():
        # Belt-and-braces: a missing file should never reach prod (CI
        # regenerates), but if it somehow does we want a clean 500 not
        # an opaque FileResponse stack trace.
        logger.error("template missing on disk: %s", _TEMPLATE_PATH)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "上传模板文件未部署 — 请联系管理员",
        )
    return FileResponse(
        path=_TEMPLATE_PATH,
        media_type=_XLSX_MIME,
        filename="product_upload_template.xlsx",
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/workbench/products/upload", response_model=WorkflowBatch)
async def upload_workbench_product_file(
    db: DbDep,
    user: ProductUploader,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Step 1: persist one xlsx original and create a workflow-v2 batch."""
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少文件名")
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "产品数据上传只接受 .xlsx 文件")
    blob = await file.read()
    if not blob:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件为空")
    if len(blob) > settings.MAX_UPLOAD_SIZE:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"文件超过 {settings.MAX_UPLOAD_SIZE // (1024 * 1024)}MB",
        )

    storage = get_storage()
    folder = f"data-upload/products/{user.id}/{uuid4().hex}"
    try:
        storage_key = storage.upload(folder, filename, blob, content_type=_XLSX_MIME)
    except Exception as exc:
        logger.exception("workbench original-file storage failed")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "文件暂时无法保存，请稍后重试；产品数据尚未写入",
        ) from exc
    try:
        batch = parse_excel(
            db,
            file_bytes=blob,
            filename=filename,
            file_url=storage_key,
            file_sha256=hashlib.sha256(blob).hexdigest(),
            user_id=user.id,
            strict_headers=False,
            workflow_version=2,
        )
    except ParseError as exc:
        storage.delete(storage_key)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"解析失败：{exc}") from exc
    except Exception as exc:
        storage.delete(storage_key)
        logger.exception("workbench upload parse crashed")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "上传处理失败") from exc
    return get_workflow_batch(db, batch_id=batch.id, user_id=user.id)


@router.post("/workbench/batches/{batch_id}/validate", response_model=WorkflowBatch)
def validate_workbench_product_batch(
    batch_id: int, db: DbDep, user: ProductUploader
) -> dict[str, Any]:
    """Step 2: run deterministic validation and matching without an LLM."""
    try:
        return validate_workflow_batch(db, batch_id=batch_id, user_id=user.id)
    except Exception as exc:
        if not isinstance(exc, (BatchNotFound, BatchOwnedByOther, BatchInWrongState, ValueError)):
            logger.exception("workbench validate crashed")
        raise _workflow_http_error(exc) from exc


@router.get("/workbench/batches/{batch_id}", response_model=WorkflowBatch)
def read_workbench_product_batch(
    batch_id: int, db: DbDep, user: ProductUploader
) -> dict[str, Any]:
    try:
        return get_workflow_batch(db, batch_id=batch_id, user_id=user.id)
    except Exception as exc:
        raise _workflow_http_error(exc) from exc


@router.get("/workbench/batches", response_model=WorkflowBatchPage)
def list_workbench_product_batches(
    db: DbDep,
    user: ProductUploader,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    return list_workflow_batches(
        db, user_id=user.id, page=page, page_size=page_size
    )


@router.get("/workbench/batches/{batch_id}/original-url")
def get_workbench_original_url(
    batch_id: int, db: DbDep, user: ProductUploader
) -> dict[str, str]:
    try:
        key = get_workflow_file_key(db, batch_id=batch_id, user_id=user.id)
        return {"url": get_storage().get_signed_url(key, expires_in=900)}
    except Exception as exc:
        raise _workflow_http_error(exc) from exc


@router.get("/workbench/batches/{batch_id}/rows", response_model=WorkflowRowsPage)
def read_workbench_product_rows(
    batch_id: int,
    db: DbDep,
    user: ProductUploader,
    view: str = "all",
    page: int = 1,
    page_size: int = 50,
    changed_only: bool = True,
) -> dict[str, Any]:
    try:
        return get_workflow_rows(
            db,
            batch_id=batch_id,
            user_id=user.id,
            view=view,
            page=page,
            page_size=page_size,
            changed_only=changed_only,
        )
    except Exception as exc:
        raise _workflow_http_error(exc) from exc


@router.post("/workbench/batches/{batch_id}/commit", response_model=CommitResult)
def commit_workbench_product_batch(
    batch_id: int, db: DbDep, user: ProductUploader
) -> dict[str, Any]:
    """Step 4: the only write action in the new workflow."""
    try:
        return commit_validated_batch(db, batch_id=batch_id, user_id=user.id)
    except Exception as exc:
        if not isinstance(
            exc,
            (BatchNotFound, BatchOwnedByOther, BatchInWrongState, BatchValidationFailed),
        ):
            logger.exception("workbench commit crashed")
        raise _workflow_http_error(exc) from exc


@router.post("/workbench/batches/{batch_id}/cancel", response_model=CancelResult)
def cancel_workbench_product_batch(
    batch_id: int, db: DbDep, user: ProductUploader
) -> dict[str, Any]:
    try:
        return cancel_batch(db, batch_id=batch_id, user_id=user.id)
    except Exception as exc:
        raise _workflow_http_error(exc) from exc


@router.post("/workbench/batches/{batch_id}/rollback")
def rollback_workbench_product_batch(
    batch_id: int, db: DbDep, user: ProductUploader
) -> dict[str, Any]:
    """Rollback entry for completed batches; revision checks remain authoritative."""
    try:
        return rollback_batch(db, batch_id=batch_id, user_id=user.id)
    except Exception as exc:
        raise _workflow_http_error(exc) from exc
