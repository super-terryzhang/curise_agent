"""DB access for the document domain."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

from domains.document.models import Document


def get(db: Session, document_id: int) -> Document | None:
    return db.get(Document, document_id)


def full_text_search(
    db: Session,
    *,
    user_id: int | None,
    include_all_users: bool,
    terms: list[str],
    doc_type: str | None = None,
    limit: int = 5,
) -> list[Document]:
    """OR-search across summary, content_markdown, tags, user_tags for
    every term. Returns ORM rows; the caller is responsible for snippet
    extraction. No COUNT — the agent's UI shows individual hits, not
    aggregate totals, for this tool.
    """
    if not terms:
        return []
    field_filters = []
    for t in terms:
        like = f"%{t}%"
        field_filters.append(Document.summary.ilike(like))
        field_filters.append(Document.content_markdown.ilike(like))
        field_filters.append(cast(Document.tags, String).ilike(like))
        field_filters.append(cast(Document.user_tags, String).ilike(like))

    stmt = select(Document).where(or_(*field_filters))
    # 2026-07-03 (Felix): documents are company-wide assets, not per-user.
    # `include_all_users` + `user_id` params kept for API stability but
    # they no longer gate visibility. Read access is uniform for every
    # logged-in user; destructive ops still enforce uploader/admin in
    # the service layer.
    del include_all_users, user_id  # intentionally unused
    if doc_type:
        stmt = stmt.where(Document.doc_type == doc_type)
    stmt = stmt.limit(limit)
    return list(db.execute(stmt).scalars())


_FOLDER_FILTER_UNSPECIFIED = object()


def list_page(
    db: Session,
    *,
    user_id: int | None = None,
    include_all_users: bool = False,
    status: str | None = None,
    doc_type: str | None = None,
    filename_like: str | None = None,
    # Sentinel-distinguished tri-state:
    #   _UNSPECIFIED   → don't filter on folder at all (default)
    #   None           → only docs at root level (folder_id IS NULL)
    #   <int>          → only docs in that folder
    folder_id: object = _FOLDER_FILTER_UNSPECIFIED,
    limit: int = 20,
    offset: int = 0,
) -> tuple[int, list[Document]]:
    stmt = select(Document)
    # 2026-07-03 (Felix): documents are company-wide — read access is
    # uniform across users. `include_all_users` + `user_id` params kept
    # for backward compat with old call sites but no longer gate results.
    del include_all_users, user_id
    if status:
        stmt = stmt.where(Document.status == status)
    if doc_type:
        stmt = stmt.where(Document.doc_type == doc_type)
    if filename_like:
        stmt = stmt.where(Document.filename.ilike(f"%{filename_like}%"))
    if folder_id is not _FOLDER_FILTER_UNSPECIFIED:
        if folder_id is None:
            stmt = stmt.where(Document.folder_id.is_(None))
        else:
            stmt = stmt.where(Document.folder_id == folder_id)

    total = len(db.execute(stmt.with_only_columns(Document.id).order_by(None)).all())
    items = list(
        db.execute(stmt.order_by(Document.created_at.desc()).limit(limit).offset(offset)).scalars()
    )
    return total, items


def create(
    db: Session,
    *,
    user_id: int,
    filename: str,
    file_url: str | None,
    file_type: str,
    file_size_bytes: int,
) -> Document:
    doc = Document(
        user_id=user_id,
        filename=filename,
        file_url=file_url,
        file_type=file_type,
        file_size_bytes=file_size_bytes,
        status="uploaded",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def save(db: Session, document: Document) -> Document:
    document.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(document)
    return document


def delete(db: Session, document: Document) -> None:
    db.delete(document)
    db.commit()
