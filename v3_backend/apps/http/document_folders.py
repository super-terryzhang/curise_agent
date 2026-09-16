"""Document folder HTTP endpoints (P3B 2026-06-21)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from apps.http._deps import CurrentUser, DbDep, Writer
from domains.document import service as doc_service
from domains.document.folders import service as folder_service
from domains.document.schemas import (
    DocumentFolderCreateRequest,
    DocumentFolderResponse,
    DocumentFolderUpdateRequest,
)


router = APIRouter(prefix="/document-folders", tags=["document-folders"])


def _is_admin(user: CurrentUser) -> bool:
    return user.role in ("superadmin", "admin")


def _translate(exc: doc_service.DocumentError) -> HTTPException:
    if isinstance(exc, doc_service.NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, doc_service.BadRequest):
        return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))


@router.get("", response_model=list[DocumentFolderResponse])
def list_folders(db: DbDep, user: CurrentUser) -> list[DocumentFolderResponse]:
    return folder_service.list_folders(
        db, user_id=user.id, is_admin=_is_admin(user)
    )


@router.post("", response_model=DocumentFolderResponse, status_code=201)
def create_folder(
    body: DocumentFolderCreateRequest, db: DbDep, user: Writer
) -> DocumentFolderResponse:
    try:
        return folder_service.create_folder(
            db,
            user_id=user.id,
            name=body.name,
            parent_folder_id=body.parent_folder_id,
            color=body.color,
        )
    except doc_service.DocumentError as exc:
        raise _translate(exc) from exc


@router.patch("/{folder_id}", response_model=DocumentFolderResponse)
def update_folder(
    folder_id: int,
    body: DocumentFolderUpdateRequest,
    db: DbDep,
    user: Writer,
) -> DocumentFolderResponse:
    dumped = body.model_dump(exclude_unset=True)
    try:
        return folder_service.update_folder(
            db,
            folder_id=folder_id,
            user_id=user.id,
            is_admin=_is_admin(user),
            name=body.name,
            parent_folder_id=body.parent_folder_id,
            parent_set="parent_folder_id" in dumped,
            color=body.color,
            color_set="color" in dumped,
        )
    except doc_service.DocumentError as exc:
        raise _translate(exc) from exc


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_folder(folder_id: int, db: DbDep, user: Writer):
    # No `-> None` annotation — clashes with 204 in FastAPI ≥ 0.111.
    try:
        folder_service.delete_folder(
            db,
            folder_id=folder_id,
            user_id=user.id,
            is_admin=_is_admin(user),
        )
    except doc_service.DocumentError as exc:
        raise _translate(exc) from exc
