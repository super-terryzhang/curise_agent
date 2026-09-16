"""Document folder service — CRUD + tree integrity (P3B 2026-06-21).

Reused error types from `domains.document.service` so HTTP-layer error
translation in `apps/http/documents.py` covers both. The folder
operations themselves stay independent of document service to keep the
domain seams clean.

Invariants enforced here (not in DB):
    1. A folder cannot be its own ancestor (cycle prevention on
       create/re-parent).
    2. A folder cannot have the same name as a sibling under the same
       parent (within a single user's scope).
    3. Delete refuses if the folder has children OR contains documents
       — UX preference over silent cascade so users don't accidentally
       wipe nested work.
    4. All operations are scoped by `user_id`. Admins can pass
       `is_admin=True` to bypass.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from domains.document.models import Document, DocumentFolder
from domains.document.schemas import DocumentFolderResponse
from domains.document.service import BadRequest, NotFound


_MAX_DEPTH = 10  # Practical guardrail; nobody needs 11-level nesting.

# Allowed color palette (mirrors v3-frontend/src/lib/folder-color.ts). Kept
# in sync manually — small enough to review; large enough that we don't
# rebuild it every schema change. Callers can also pass a raw `#RRGGBB`
# hex string; validation accepts anything matching that shape so power
# users aren't locked to the swatches.
_ALLOWED_COLORS = frozenset({
    "#ef4444", "#f97316", "#f59e0b", "#eab308",
    "#84cc16", "#22c55e", "#10b981", "#06b6d4",
    "#3b82f6", "#6366f1", "#8b5cf6", "#ec4899",
})


def _validate_color(value: str) -> str:
    """Accept a preset swatch or a `#RRGGBB` hex string; normalize to
    lowercase. Raises `BadRequest` on anything else."""
    import re

    cleaned = value.strip().lower()
    if cleaned in _ALLOWED_COLORS:
        return cleaned
    if re.fullmatch(r"#[0-9a-f]{6}", cleaned):
        return cleaned
    raise BadRequest("颜色格式不合法（需为预设或 #RRGGBB 十六进制）")


def list_folders(
    db: Session, *, user_id: int, is_admin: bool
) -> list[DocumentFolderResponse]:
    """Return ALL folders (company-wide). The frontend builds the tree
    locally — server returns a flat list keyed by `parent_folder_id` to
    avoid recursive-CTE cost on Postgres.

    2026-07-03: folders + documents are treated as company assets.
    Every logged-in user sees the full tree; `user_id` + `is_admin`
    params are kept for API stability but no longer filter.
    """
    del user_id, is_admin
    folders = (
        db.query(DocumentFolder).order_by(DocumentFolder.name).all()
    )

    # Compute `document_count` per folder in one batched query rather
    # than N+1 SELECTs. The dict is keyed by folder_id; missing keys
    # mean zero docs in that folder.
    counts: dict[int, int] = dict(
        db.query(Document.folder_id, func.count(Document.id))
        .filter(Document.folder_id.is_not(None))
        .group_by(Document.folder_id)
        .all()
    )

    return [
        DocumentFolderResponse(
            id=f.id,
            name=f.name,
            parent_folder_id=f.parent_folder_id,
            document_count=counts.get(f.id, 0),
            color=f.color,
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f in folders
    ]


def create_folder(
    db: Session,
    *,
    user_id: int,
    name: str,
    parent_folder_id: int | None,
    color: str | None = None,
) -> DocumentFolderResponse:
    cleaned = (name or "").strip()
    if not cleaned:
        raise BadRequest("文件夹名称不能为空")
    if len(cleaned) > 200:
        raise BadRequest("文件夹名称过长（上限 200 字符）")

    # Parent must exist (or be NULL for root). Folders are company-
    # wide (2026-07-03) so anyone can nest under any parent.
    if parent_folder_id is not None:
        parent = _load_any_folder(db, parent_folder_id)
        if _depth_of(db, parent) >= _MAX_DEPTH:
            raise BadRequest(f"文件夹嵌套深度超过 {_MAX_DEPTH} 层")

    if _sibling_exists(db, parent_id=parent_folder_id, name=cleaned):
        raise BadRequest(f"同级已存在名为「{cleaned}」的文件夹")

    validated_color = _validate_color(color) if color else None

    folder = DocumentFolder(
        user_id=user_id,
        name=cleaned,
        parent_folder_id=parent_folder_id,
        color=validated_color,
    )
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return DocumentFolderResponse(
        id=folder.id,
        name=folder.name,
        parent_folder_id=folder.parent_folder_id,
        document_count=0,
        color=folder.color,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


def update_folder(
    db: Session,
    *,
    folder_id: int,
    user_id: int,
    is_admin: bool,
    name: str | None,
    parent_folder_id: int | None,
    parent_set: bool,
    color: str | None = None,
    color_set: bool = False,
) -> DocumentFolderResponse:
    """Rename and/or re-parent and/or recolor.

    `parent_set=True` / `color_set=True` distinguish "user explicitly sent
    null to clear the field" from "user didn't touch it" (both of which
    Pydantic collapses to None). Caller does
    `body.model_dump(exclude_unset=True)` and passes
    `parent_set="parent_folder_id" in dumped` (same idea for color).
    """
    del user_id, is_admin  # rename / reparent / recolor is company-wide
    folder = _load_any_folder(db, folder_id)

    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise BadRequest("文件夹名称不能为空")
        if len(cleaned) > 200:
            raise BadRequest("文件夹名称过长（上限 200 字符）")
        if cleaned != folder.name and _sibling_exists(
            db,
            parent_id=folder.parent_folder_id,
            name=cleaned,
            exclude_id=folder.id,
        ):
            raise BadRequest(f"同级已存在名为「{cleaned}」的文件夹")
        folder.name = cleaned

    if parent_set:
        new_parent_id = parent_folder_id
        if new_parent_id == folder.id:
            raise BadRequest("文件夹不能成为自身的父级")
        if new_parent_id is not None:
            new_parent = _load_any_folder(db, new_parent_id)
            if _is_descendant(db, ancestor_id=folder.id, candidate=new_parent):
                raise BadRequest("不能把文件夹移动到自己的子级下（会成环）")
            if _depth_of(db, new_parent) >= _MAX_DEPTH:
                raise BadRequest(f"文件夹嵌套深度超过 {_MAX_DEPTH} 层")
        # Check name collision at the new location.
        if _sibling_exists(
            db,
            parent_id=new_parent_id,
            name=folder.name,
            exclude_id=folder.id,
        ):
            raise BadRequest(f"目标位置已存在名为「{folder.name}」的文件夹")
        folder.parent_folder_id = new_parent_id

    if color_set:
        folder.color = _validate_color(color) if color else None

    db.commit()
    db.refresh(folder)
    return _to_response(db, folder)


def delete_folder(
    db: Session, *, folder_id: int, user_id: int, is_admin: bool
) -> None:
    """Refuse delete if folder is non-empty. Forces user to relocate
    children first — silent cascade is the kind of footgun that loses
    real work in this domain."""
    folder = _load_for_user(db, folder_id, user_id, is_admin)

    child_folders = (
        db.query(DocumentFolder.id)
        .filter(DocumentFolder.parent_folder_id == folder.id)
        .first()
    )
    if child_folders:
        raise BadRequest("请先清空子文件夹再删除")

    child_docs = (
        db.query(Document.id).filter(Document.folder_id == folder.id).first()
    )
    if child_docs:
        raise BadRequest("文件夹下仍有文档，请先移动或删除")

    db.delete(folder)
    db.commit()


def move_document(
    db: Session,
    *,
    document_id: int,
    folder_id: int | None,
    user_id: int,
    is_admin: bool,
) -> Document:
    """Set `Document.folder_id`. `None` = back to root. Returns the
    reloaded Document so the HTTP layer can serialize it through the
    standard `_serialize_detail` flow."""
    from domains.document import repository as doc_repo

    del user_id, is_admin  # move is company-wide (2026-07-03)
    doc = doc_repo.get(db, document_id)
    if doc is None:
        raise NotFound("文档不存在")

    if folder_id is not None:
        # Just verify the target folder exists — no ownership check.
        # Documents + folders are company-wide assets; anyone can file
        # anything into anything. The uploader stays on the document
        # as audit info; the folder creator stays on the folder as audit
        # info. Neither field gates the move.
        _load_any_folder(db, folder_id)

    doc.folder_id = folder_id
    doc_repo.save(db, doc)
    return doc


# ─── Internal helpers ───────────────────────────────────────────


def _load_any_folder(db: Session, folder_id: int) -> DocumentFolder:
    """Fetch a folder without ownership check — company-wide access
    (2026-07-03)."""
    folder = db.get(DocumentFolder, folder_id)
    if folder is None:
        raise NotFound("文件夹不存在")
    return folder


def _load_for_folder_creator(
    db: Session, folder_id: int, user_id: int, is_admin: bool
) -> DocumentFolder:
    """Only creator + admin can operate on this folder. Used by delete."""
    folder = _load_any_folder(db, folder_id)
    if not is_admin and folder.user_id != user_id:
        raise NotFound("文件夹不存在")
    return folder


# Backward-compat: legacy callers within this module still use the
# name `_load_for_user`; new sites pick the specific helper above.
_load_for_user = _load_for_folder_creator


def _sibling_exists(
    db: Session,
    *,
    parent_id: int | None,
    name: str,
    exclude_id: int | None = None,
) -> bool:
    """Company-wide uniqueness (2026-07-03): (parent, name) is now the
    unique key — no more per-user namespaces. Two admins can't both
    create「测试」under the root, only the first sticks."""
    q = db.query(DocumentFolder.id).filter(DocumentFolder.name == name)
    if parent_id is None:
        q = q.filter(DocumentFolder.parent_folder_id.is_(None))
    else:
        q = q.filter(DocumentFolder.parent_folder_id == parent_id)
    if exclude_id is not None:
        q = q.filter(DocumentFolder.id != exclude_id)
    return q.first() is not None


def _depth_of(db: Session, folder: DocumentFolder) -> int:
    """Distance from `folder` to root. Root-level folder has depth 1."""
    depth = 1
    current = folder
    while current.parent_folder_id is not None:
        depth += 1
        if depth > _MAX_DEPTH + 1:
            # Defensive: bad data could cause infinite walk.
            return depth
        parent = db.get(DocumentFolder, current.parent_folder_id)
        if parent is None:
            return depth
        current = parent
    return depth


def _is_descendant(
    db: Session, *, ancestor_id: int, candidate: DocumentFolder
) -> bool:
    """True if `candidate` is reachable by walking up from
    `ancestor_id`. Used to block "move folder X under one of X's
    descendants" which would create a cycle."""
    current: DocumentFolder | None = candidate
    seen: set[int] = set()
    while current is not None and current.id not in seen:
        if current.id == ancestor_id:
            return True
        seen.add(current.id)
        if current.parent_folder_id is None:
            return False
        current = db.get(DocumentFolder, current.parent_folder_id)
    return False


def _to_response(
    db: Session, folder: DocumentFolder
) -> DocumentFolderResponse:
    count = (
        db.query(func.count(Document.id))
        .filter(Document.folder_id == folder.id)
        .scalar()
        or 0
    )
    return DocumentFolderResponse(
        id=folder.id,
        name=folder.name,
        parent_folder_id=folder.parent_folder_id,
        document_count=count,
        color=folder.color,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )
