"""Document HTTP endpoints — `/api/documents/*` compatible with v2 frontend."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from apps.http._deps import CurrentUser, DbDep, Writer
from domains.document import service
from domains.document.schemas import (
    DocumentCreateOrderRequest,
    DocumentDetailResponse,
    DocumentFolderCreateRequest,
    DocumentFolderResponse,
    DocumentFolderUpdateRequest,
    DocumentMetadataUpdateRequest,
    DocumentMoveRequest,
    DocumentRenameRequest,
    DocumentResponse,
    DocumentTypeUpdateRequest,
    DocumentUserTagAddRequest,
    OrderPayloadResponse,
    PaginatedDocumentsResponse,
)
from domains.orders import service as orders_service
from domains.orders.errors import BadRequest as OrderBadRequest
from domains.orders.errors import NotFound as OrderNotFound
from domains.orders.errors import OrderError, StatusConflict
from domains.orders.schemas import OrderDetail
from infrastructure.storage import get_storage

router = APIRouter(prefix="/documents", tags=["documents"])


def _translate(exc: service.DocumentError) -> HTTPException:
    if isinstance(exc, service.NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, service.BadRequest):
        return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    if isinstance(exc, service.StatusConflict):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))


def _is_admin(user: CurrentUser) -> bool:
    return user.role in ("superadmin", "admin")


# ═════ List / Upload ══════════════════════════════════════════


@router.get("", response_model=PaginatedDocumentsResponse)
def list_documents(
    db: DbDep,
    user: CurrentUser,
    status: str | None = Query(default=None),
    tag: list[str] | None = Query(
        default=None,
        description=(
            "Filter docs whose `tags` or `user_tags` contains all listed values "
            "(AND semantics). Repeat the param: ?tag=foo&tag=bar."
        ),
    ),
    # P3B (2026-06-21): folder filter.
    #   `?folder_id=5`       → only docs in folder 5
    #   `?folder_id=root`    → only docs at root level (folder_id IS NULL)
    #   absent               → no folder filter (legacy behavior)
    folder_id: str | None = Query(
        default=None,
        description="Folder filter. Pass an int for a specific folder, 'root' for unfiled docs, or omit for all.",
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PaginatedDocumentsResponse:
    # Translate the string query into the service's sentinel-distinguished
    # tri-state. Bad ints become "no filter" rather than 400 — UI may
    # construct URLs from stale state and silently degrading to "show all"
    # is friendlier than a hard error.
    folder_arg: object = service._FOLDER_FILTER_UNSPECIFIED
    if folder_id == "root":
        folder_arg = None
    elif folder_id is not None:
        try:
            folder_arg = int(folder_id)
        except ValueError:
            folder_arg = service._FOLDER_FILTER_UNSPECIFIED

    return service.list_documents(
        db,
        user_id=user.id,
        is_admin=_is_admin(user),
        status=status,
        tags=tag,
        folder_id=folder_arg,
        limit=limit,
        offset=offset,
    )


@router.get("/user-tags", response_model=list[dict[str, Any]])
def list_user_tags(db: DbDep, user: CurrentUser) -> list[dict[str, Any]]:
    """All user-managed tags + per-tag document count. Sorted by name.
    Powers the tag-filter sidebar on the document list page."""
    return service.list_user_tags(db, user_id=user.id, is_admin=_is_admin(user))


@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    db: DbDep,
    user: Writer,
    file: UploadFile = File(...),
    is_purchase_order: bool = Form(False),  # noqa: ARG001 (Phase 3 uses this hint)
) -> DocumentResponse:
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件名不能为空")
    content = await file.read()
    try:
        return service.upload_document(
            db,
            user_id=user.id,
            filename=file.filename,
            content=content,
            content_type=file.content_type,
        )
    except service.DocumentError as exc:
        raise _translate(exc) from exc


# ═════ Detail / Update / Delete ═══════════════════════════════


@router.get("/{document_id}", response_model=DocumentDetailResponse)
def get_document(document_id: int, db: DbDep, user: CurrentUser) -> DocumentDetailResponse:
    try:
        return service.get_document(
            db, document_id=document_id, user_id=user.id, is_admin=_is_admin(user)
        )
    except service.DocumentError as exc:
        raise _translate(exc) from exc


@router.patch("/{document_id}", response_model=DocumentDetailResponse)
def update_document_type(
    document_id: int,
    body: DocumentTypeUpdateRequest,
    db: DbDep,
    user: Writer,
) -> DocumentDetailResponse:
    try:
        return service.update_doc_type(
            db,
            document_id=document_id,
            user_id=user.id,
            is_admin=_is_admin(user),
            doc_type=body.doc_type,
        )
    except service.DocumentError as exc:
        raise _translate(exc) from exc


@router.post("/{document_id}/reextract", response_model=DocumentDetailResponse)
def reextract_document(
    document_id: int,
    db: DbDep,
    user: Writer,
) -> DocumentDetailResponse:
    """Re-run the extraction pipeline on an already-uploaded document.

    Used by the detail page's "重新提取" button when status=error or when
    a user wants to re-run after fixing classifier rules / Gemini config.
    Sets status=extracting and schedules the workflow on the runner;
    polling picks up the terminal state.
    """
    try:
        return service.reextract(
            db, document_id=document_id, user_id=user.id, is_admin=_is_admin(user)
        )
    except service.DocumentError as exc:
        raise _translate(exc) from exc


@router.patch(
    "/{document_id}/folder", response_model=DocumentDetailResponse
)
def move_document_to_folder(
    document_id: int,
    body: DocumentMoveRequest,
    db: DbDep,
    user: Writer,
) -> DocumentDetailResponse:
    """Move a document into a folder. `folder_id: null` = back to root.

    Separate endpoint from PATCH /{id} so the schemas stay focused — a
    doc-type change and a folder move are conceptually independent and
    auditing/permissions may diverge later.
    """
    from domains.document.folders import service as folder_service

    try:
        folder_service.move_document(
            db,
            document_id=document_id,
            folder_id=body.folder_id,
            user_id=user.id,
            is_admin=_is_admin(user),
        )
    except service.DocumentError as exc:
        raise _translate(exc) from exc
    # Reload via the canonical detail flow so the response is identical
    # to GET /{id} — same shape, same fields, same serializer.
    return service.get_document(
        db, document_id=document_id, user_id=user.id, is_admin=_is_admin(user)
    )


@router.patch(
    "/{document_id}/display-name", response_model=DocumentDetailResponse
)
def rename_document(
    document_id: int,
    body: DocumentRenameRequest,
    db: DbDep,
    user: Writer,
) -> DocumentDetailResponse:
    """Set or clear the user-facing display_name.

    Distinct from PATCH /{id} (which mutates doc_type) so the two
    surfaces have independent permission and validation paths. The body
    accepts `display_name: null` to revert to the original filename.
    """
    try:
        return service.update_display_name(
            db,
            document_id=document_id,
            user_id=user.id,
            is_admin=_is_admin(user),
            display_name=body.display_name,
        )
    except service.DocumentError as exc:
        raise _translate(exc) from exc


@router.post("/{document_id}/user-tags", response_model=DocumentDetailResponse)
def add_user_tag(
    document_id: int,
    body: DocumentUserTagAddRequest,
    db: DbDep,
    user: Writer,
) -> DocumentDetailResponse:
    """Append a user-managed tag to the document. Idempotent + dedupes
    against existing tags. Tags are normalized to lowercase + kebab-case."""
    try:
        return service.add_user_tag(
            db,
            document_id=document_id,
            user_id=user.id,
            is_admin=_is_admin(user),
            tag=body.tag,
        )
    except service.DocumentError as exc:
        raise _translate(exc) from exc


@router.delete("/{document_id}/user-tags/{tag}", response_model=DocumentDetailResponse)
def remove_user_tag(
    document_id: int,
    tag: str,
    db: DbDep,
    user: Writer,
) -> DocumentDetailResponse:
    """Remove a user-managed tag (case-insensitive). No-op if not present
    so double-click / network retries don't 404."""
    try:
        return service.remove_user_tag(
            db,
            document_id=document_id,
            user_id=user.id,
            is_admin=_is_admin(user),
            tag=tag,
        )
    except service.DocumentError as exc:
        raise _translate(exc) from exc


@router.delete("/{document_id}")
def delete_document(
    document_id: int,
    db: DbDep,
    user: Writer,
    force: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        return service.delete_document(
            db,
            document_id=document_id,
            user_id=user.id,
            is_admin=_is_admin(user),
            force=force,
        )
    except service.DocumentError as exc:
        raise _translate(exc) from exc


# ═════ Order payload — delegates to orders domain (Phase 3) ═════


def _translate_order_error(exc: OrderError) -> HTTPException:
    if isinstance(exc, OrderNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, StatusConflict):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, OrderBadRequest):
        return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))


@router.get("/{document_id}/order-payload", response_model=OrderPayloadResponse)
def get_order_payload(document_id: int, db: DbDep, user: CurrentUser) -> OrderPayloadResponse:
    try:
        data = orders_service.build_order_payload_for_document(
            db, document_id=document_id, user_id=user.id, is_admin=_is_admin(user)
        )
    except OrderError as exc:
        raise _translate_order_error(exc) from exc
    return OrderPayloadResponse(**data)


@router.patch("/{document_id}/metadata", response_model=OrderPayloadResponse)
def update_document_metadata(
    document_id: int,
    body: DocumentMetadataUpdateRequest,
    db: DbDep,
    user: Writer,
) -> OrderPayloadResponse:
    try:
        service.apply_metadata_overrides(
            db,
            document_id=document_id,
            user_id=user.id,
            is_admin=_is_admin(user),
            fields=body.fields,
        )
    except service.DocumentError as exc:
        raise _translate(exc) from exc
    # Return the fresh order payload (via orders domain) so the frontend
    # immediately sees the merged state.
    try:
        payload = orders_service.build_order_payload_for_document(
            db, document_id=document_id, user_id=user.id, is_admin=_is_admin(user)
        )
    except OrderError as exc:
        raise _translate_order_error(exc) from exc
    return OrderPayloadResponse(**payload)


@router.post("/{document_id}/create-order", response_model=OrderDetail)
def create_order_from_document(
    document_id: int,
    body: DocumentCreateOrderRequest,
    db: DbDep,
    user: Writer,
) -> OrderDetail:
    """Create an Order from a Document.

    With `ASYNC_CREATE_ORDER` on (default), returns immediately with
    `status="matching"` and submits the Gemini matching pipeline to the
    background job runner. The frontend navigates to the order detail
    page and its existing 2s poll picks up the status transition.
    Mirrors the pattern used by document upload (line 103) for
    `run_document_pipeline`.
    """
    from apps.jobs.runner import get_job_runner
    from infrastructure.config import settings

    try:
        detail = orders_service.create_from_document(
            db,
            document_id=document_id,
            user_id=user.id,
            is_admin=_is_admin(user),
            force=body.force,
        )
    except OrderError as exc:
        raise _translate_order_error(exc) from exc

    # Only submit the background job when we're on the async path AND the
    # order is actually in the "matching" state. The legacy sync path
    # already returns ready/error inline; double-submitting would re-run
    # matching for nothing.
    if settings.ASYNC_CREATE_ORDER and detail.status == "matching":
        get_job_runner().submit(orders_service._run_matching_for_order, detail.id)

    return detail


# ═════ File streaming (dev-friendly local storage only) ══════════


@router.get("/{document_id}/file")
def stream_document_file(document_id: int, db: DbDep, user: CurrentUser) -> Response:
    """Return the original uploaded file bytes.

    Dev uses local filesystem; prod uses Supabase signed URLs via `preview_url`
    on the list / detail responses. This endpoint is a fallback for local use.
    """
    try:
        doc_detail = service.get_document(
            db, document_id=document_id, user_id=user.id, is_admin=_is_admin(user)
        )
    except service.DocumentError as exc:
        raise _translate(exc) from exc
    if not doc_detail.file_url:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档文件不存在")
    content = get_storage().download(doc_detail.file_url)
    from domains.document.file_types import default_content_type
    mime = default_content_type(doc_detail.file_type or "")
    return StreamingResponse(iter([content]), media_type=mime)
