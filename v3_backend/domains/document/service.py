"""Document business logic — the only entry point for HTTP / Agent / CLI."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from domains.document import repository, workflow
from domains.document.models import Document
from domains.document.schemas import (
    DocumentDetailResponse,
    DocumentResponse,
    OrderPayloadResponse,
    PaginatedDocumentsResponse,
)
from infrastructure.jobs.runner import get_job_runner
from infrastructure.storage import get_storage

logger = logging.getLogger(__name__)


class DocumentError(Exception):
    pass


class NotFound(DocumentError):
    pass


class BadRequest(DocumentError):
    pass


class StatusConflict(DocumentError):
    """Operation not valid for the document's current status."""


ALLOWED_DOC_TYPES: frozenset[str] = frozenset({"purchase_order", "invoice", "quote", "unknown"})

_MAX_UPLOAD_SIZE = 30 * 1024 * 1024  # 30 MB


# ═════ Upload ═══════════════════════════════════════════════════


def upload_document(
    db: Session,
    *,
    user_id: int,
    filename: str,
    content: bytes,
    content_type: str | None = None,
) -> DocumentResponse:
    """Persist an uploaded file + schedule the extraction pipeline.

    Whitelist policy lives in `domains.document.file_types`. Anything
    not in that table — videos, executables, archives, unknown extensions —
    is rejected here. Files of supported types but without a registered
    extractor (e.g. .docx today) still upload and store; the workflow
    marks them `status="stored"` rather than `error` so they're not
    presented to the user as failures.
    """
    from domains.document.file_types import (
        default_content_type,
        detect_file_type,
        supported_extensions,
    )

    if not filename:
        raise BadRequest("文件名不能为空")
    if not content:
        raise BadRequest("文件内容不能为空")
    if len(content) > _MAX_UPLOAD_SIZE:
        raise BadRequest("文件大小不能超过 30 MB")

    file_type = detect_file_type(filename)
    if file_type is None:
        raise BadRequest(
            "不支持的文件类型。允许：" + " / ".join(supported_extensions())
        )

    storage = get_storage()
    storage_key = storage.upload(
        "documents",
        filename,
        content,
        content_type=content_type or default_content_type(file_type),
    )
    doc = repository.create(
        db,
        user_id=user_id,
        filename=filename,
        file_url=storage_key,
        file_type=file_type,
        file_size_bytes=len(content),
    )

    # Schedule background extraction. SynchronousRunner (used in tests) blocks
    # until completion; AsyncioRunner returns immediately.
    runner = get_job_runner()
    runner.submit(workflow.run_document_pipeline, doc.id)

    # Re-fetch so the caller sees the latest status (the pipeline may have
    # already written `extracted`/`error` when a sync runner is in use).
    db.refresh(doc)
    return _serialize(db, doc)


def store_source_document(
    db: Session, *, user_id: int, filename: str, content: bytes,
    source_key: str, version_key: str, folder_id: int | None = None,
) -> Document:
    """Store an Oracle PDF without starting a second, unrelated background task.

    Caller commits the Document and its source association in one transaction.
    Analysis is explicitly awaited by the source import service.
    """
    import re

    from domains.document.models import DocumentFolder

    if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-4096:]:
        raise BadRequest("采购订单 PDF 不完整")
    if len(content) > _MAX_UPLOAD_SIZE:
        raise BadRequest("文件大小不能超过 30 MB")
    if not all(re.fullmatch(r"[0-9a-f]{64}", key) for key in (source_key, version_key)):
        raise BadRequest("来源标识无效")
    if folder_id is not None:
        folder = db.get(DocumentFolder, folder_id)
        if folder is None or folder.user_id != user_id:
            raise NotFound("导入文件夹不存在或不属于导入账号")
    key = get_storage().upload(
        f"documents/oracle/{source_key}/{version_key}", filename, content,
        content_type="application/pdf",
    )
    doc = Document(user_id=user_id, filename=filename, file_url=key, file_type="pdf",
                   file_size_bytes=len(content), folder_id=folder_id,
                   doc_type="purchase_order", status="uploaded", user_tags=["oracle"],
                   tags=["source:oracle", "download:automatic"])
    db.add(doc)
    db.flush()
    return doc


# ═════ Query ════════════════════════════════════════════════════


_FOLDER_FILTER_UNSPECIFIED = repository._FOLDER_FILTER_UNSPECIFIED


def list_documents(
    db: Session,
    *,
    user_id: int,
    is_admin: bool,
    status: str | None = None,
    doc_type: str | None = None,
    filename_like: str | None = None,
    tags: list[str] | None = None,
    # See repository.list_page for the tri-state semantics. Default is
    # "no folder filter" so existing callers behave unchanged.
    folder_id: object = _FOLDER_FILTER_UNSPECIFIED,
    limit: int = 20,
    offset: int = 0,
) -> PaginatedDocumentsResponse:
    total, items = repository.list_page(
        db,
        user_id=user_id,
        include_all_users=is_admin,
        status=status,
        doc_type=doc_type,
        filename_like=filename_like,
        folder_id=folder_id,
        limit=limit,
        offset=offset,
    )
    cleaned = [t.strip().lower() for t in (tags or []) if t and t.strip()]
    if cleaned:
        # AND semantics: a document must contain EVERY requested tag (in
        # either `tags` or `user_tags`). Filtered in Python — the page
        # is bounded (≤ 50 rows) and JSON-array containment in SQL is
        # dialect-dependent (SQLite vs Postgres), so the cost is fine.
        def doc_tag_set(d: Document) -> set[str]:
            out: set[str] = set()
            for arr in (d.tags or [], d.user_tags or []):
                for t in arr:
                    if isinstance(t, str):
                        out.add(t.lower())
            return out

        matched = [d for d in items if all(t in doc_tag_set(d) for t in cleaned)]
        return PaginatedDocumentsResponse(
            total=len(matched),
            items=[_serialize(db, item) for item in matched],
        )
    return PaginatedDocumentsResponse(
        total=total,
        items=[_serialize(db, item) for item in items],
    )


def full_text_search(
    db: Session,
    *,
    user_id: int,
    is_admin: bool,
    terms: list[str],
    doc_type: str | None = None,
    limit: int = 5,
) -> list[Document]:
    """Thin pass-through to repository.full_text_search so agent tools
    don't reach into the repository directly. Returns ORM Document rows
    — the caller handles snippet/hit serialization (LLM-shaped output)."""
    return repository.full_text_search(
        db,
        user_id=user_id,
        include_all_users=is_admin,
        terms=terms,
        doc_type=doc_type,
        limit=limit,
    )


def get_document(
    db: Session,
    *,
    document_id: int,
    user_id: int,
    is_admin: bool,
) -> DocumentDetailResponse:
    del user_id, is_admin  # read is company-wide (2026-07-03)
    doc = _load_any(db, document_id)
    return _serialize_detail(db, doc)


_DISPLAY_NAME_MAX_LEN = 255


def update_display_name(
    db: Session,
    *,
    document_id: int,
    user_id: int,
    is_admin: bool,
    display_name: str | None,
) -> DocumentDetailResponse:
    """Set / clear the user-facing rename.

    `None` clears the rename so the UI falls back to `filename`. Empty
    string and whitespace-only inputs are treated the same as `None` —
    they're indistinguishable from "user cleared the field" in practice
    and saving an empty string would render as a blank header in the UI.

    Length cap (255 chars) matches the DB column. Anything longer is
    truncated with a notice rather than rejected — frontend already
    visually caps the input, and a hard 400 mid-rename would feel
    arbitrary to the user.
    """
    del user_id, is_admin  # renames are company-wide (2026-07-03)
    doc = _load_any(db, document_id)

    if display_name is None or not display_name.strip():
        cleaned: str | None = None
    else:
        cleaned = display_name.strip()
        if len(cleaned) > _DISPLAY_NAME_MAX_LEN:
            cleaned = cleaned[:_DISPLAY_NAME_MAX_LEN]

    doc.display_name = cleaned
    repository.save(db, doc)
    return _serialize_detail(db, doc)


def update_doc_type(
    db: Session,
    *,
    document_id: int,
    user_id: int,
    is_admin: bool,
    doc_type: str,
) -> DocumentDetailResponse:
    """Set the document's classification.

    Switching TO purchase_order is the user's explicit "treat this as a PO"
    signal — that triggers the same Gemini-backed enrichment the upload
    workflow used to run automatically. Enrichment is 8-30s of LLM time, so
    by default we schedule it as a background job (mirrors
    `domains.orders.service.create_from_document`) and return immediately:
    the response carries `status="extracting"` and the frontend's existing
    document-detail polling takes over until the background flips it to
    `"extracted"` / `"error"`.

    Set `ASYNC_DOC_TYPE_ENRICH=False` for a 1-line revert if the polling UI
    ever regresses.
    """
    if doc_type not in ALLOWED_DOC_TYPES:
        raise BadRequest(f"不支持的 doc_type: {doc_type}。允许: {sorted(ALLOWED_DOC_TYPES)}")
    doc = _load_for_user(db, document_id, user_id, is_admin)
    previous_type = doc.doc_type
    doc.doc_type = doc_type

    # System tags (`doc_type:purchase_order`, `has_products:…`) need to
    # change whenever the classification flips — refresh them BEFORE the
    # background job starts so the immediate response already reflects the
    # new classification in the filter sidebar. `has_products:true` may be
    # missing until enrichment finishes; the background path re-runs this
    # so the tag appears once products land.
    if previous_type != doc_type:
        _refresh_system_tags(doc)

    needs_enrich = doc_type == "purchase_order" and previous_type != "purchase_order"
    if not needs_enrich:
        repository.save(db, doc)
        return _serialize_detail(db, doc)

    from infrastructure.config import settings

    if not settings.ASYNC_DOC_TYPE_ENRICH:
        # Legacy synchronous path — kept for emergency rollback. Same
        # behavior as before the async refactor.
        from domains.orders import enrich_purchase_order_document

        try:
            enrich_purchase_order_document(doc)
        except Exception as exc:
            logger.warning(
                "po-enrich on manual type change failed for document %d: %s",
                document_id,
                exc,
            )
            doc.processing_error = f"PO 富化失败: {exc}"
        _refresh_system_tags(doc)  # has_products may have just appeared
        repository.save(db, doc)
        if not doc.processing_error:
            from domains.orders import automatic_from_document

            automatic_from_document(document_id)
        return _serialize_detail(db, doc)

    # Async path. Persist `status="extracting"` synchronously so the
    # client sees a clean intermediate state on the immediate response;
    # the background task takes the document through to "extracted" /
    # "error". Note we must commit *before* the task starts —
    # `_run_doc_type_enrichment` opens its own SessionLocal and will not
    # see in-flight transactions on this request's session.
    doc.status = "extracting"
    doc.processing_error = None
    repository.save(db, doc)

    get_job_runner().submit(_run_doc_type_enrichment, document_id)
    return _serialize_detail(db, doc)


async def _run_doc_type_enrichment(document_id: int) -> None:
    """Background job: run Gemini enrichment on a manually-typed PO and
    flip the document's status.

    Mirrors `domains.orders.service._run_matching_for_order` — opens its
    own session because the HTTP request's session is already closed by
    the time this runs.
    """
    import asyncio

    await asyncio.to_thread(_run_doc_type_enrichment_sync, document_id)


def _run_doc_type_enrichment_sync(document_id: int) -> None:
    from infrastructure.db import session as _session_module

    with _session_module.SessionLocal() as db:
        doc = repository.get(db, document_id)
        if doc is None:
            logger.warning("doc-type enrich: document %s not found", document_id)
            return
        try:
            from domains.orders import enrich_purchase_order_document

            enrich_purchase_order_document(doc)
            # `enrich_purchase_order_document` records LLM extraction
            # failures on `doc.processing_error` without raising — promote
            # that to terminal status here so the polling UI stops.
            doc.status = "error" if doc.processing_error else "extracted"
        except Exception as exc:
            logger.exception(
                "doc-type enrich failed for document %d", document_id
            )
            doc.processing_error = f"PO 富化失败: {exc}"
            doc.status = "error"
        # has_products tag may have just appeared — refresh before saving.
        _refresh_system_tags(doc)
        repository.save(db, doc)
        if doc.status == "extracted":
            from domains.orders import automatic_from_document

            automatic_from_document(document_id)


def _refresh_system_tags(doc: Document) -> None:
    """Rebuild the `key:value` system tags in-place, preserving LLM tags.

    Called from `update_doc_type` so a manual classification updates the
    `doc_type:` tag (and adds `has_products:true` if PO enrichment just
    populated products). LLM-generated content tags stay as-is.
    """
    existing = list(doc.tags or [])
    # Drop every system-shaped tag — anything containing ':' that we own.
    _SYSTEM_KEYS = ("file_type:", "doc_type:", "extractor:", "has_products:")
    content_tags = [
        t for t in existing
        if not any(t.startswith(k) for k in _SYSTEM_KEYS)
    ]

    system_tags: list[str] = []
    if doc.file_type:
        system_tags.append(f"file_type:{doc.file_type}")
    if doc.doc_type:
        system_tags.append(f"doc_type:{doc.doc_type}")
    if doc.extraction_method:
        system_tags.append(f"extractor:{doc.extraction_method}")
    products = (doc.extracted_data or {}).get("products") or []
    if isinstance(products, list) and products:
        system_tags.append("has_products:true")

    doc.tags = system_tags + content_tags


# ═════ User tags (manually added by humans) ═══════════════════════
#
# These live in `Document.user_tags` (JSON array of strings) — separate
# from `Document.tags` so the workflow's `tags` regeneration doesn't
# trample what the user typed in. Both are unioned at filter time.

_MAX_TAGS_PER_DOC = 30
_MAX_TAG_LENGTH = 50


def _normalize_user_tag(raw: str) -> str:
    """Trim + lowercase + space-to-hyphen + drop punctuation we don't want.

    Keeping the same shape as our LLM-generated topic tags (kebab-case)
    means filter UI doesn't have to reason about two different casings.

    Rules enforced (after normalization):
      - 1-50 chars, must contain at least one alphanumeric
      - May NOT contain `:` — that prefix-shape is reserved for system
        tags (`file_type:pdf`, `doc_type:purchase_order`, …) so banning
        colons here keeps user vs system tags visually distinct.
    """
    if not raw:
        raise BadRequest("tag 不能为空")
    cleaned = raw.strip().lower()
    if not cleaned:
        raise BadRequest("tag 不能为空")
    # Reject colons up-front — the user typing `file_type:fake` should
    # see a clear error, not a silently-mangled `file-type-fake` that
    # passes the reserved-prefix check by accident.
    if ":" in cleaned:
        raise BadRequest("tag 不能含 ':'（保留给系统 tag 使用）")
    # Replace whitespace runs and underscores with single hyphens.
    out = []
    prev_dash = False
    for ch in cleaned:
        if ch.isalnum() or ch in "-.":
            out.append(ch)
            prev_dash = False
        elif ch in (" ", "_", "/"):
            if not prev_dash:
                out.append("-")
                prev_dash = True
        # Anything else (punctuation, emoji, etc.) is dropped.
    final = "".join(out).strip("-")
    if not final:
        raise BadRequest("tag 必须含有至少一个字母或数字")
    if len(final) > _MAX_TAG_LENGTH:
        raise BadRequest(f"tag 长度不能超过 {_MAX_TAG_LENGTH} 字符")
    return final


def add_user_tag(
    db: Session,
    *,
    document_id: int,
    user_id: int,
    is_admin: bool,
    tag: str,
) -> DocumentDetailResponse:
    """Append a normalized user_tag to a document. Idempotent + dedupes."""
    del user_id, is_admin  # tagging is company-wide (2026-07-03)
    cleaned = _normalize_user_tag(tag)
    doc = _load_any(db, document_id)
    existing = list(doc.user_tags or [])
    if cleaned in existing:
        return _serialize_detail(db, doc)
    if len(existing) >= _MAX_TAGS_PER_DOC:
        raise BadRequest(f"单个文档最多 {_MAX_TAGS_PER_DOC} 个用户 tag")
    doc.user_tags = [*existing, cleaned]
    repository.save(db, doc)
    return _serialize_detail(db, doc)


def remove_user_tag(
    db: Session,
    *,
    document_id: int,
    user_id: int,
    is_admin: bool,
    tag: str,
) -> DocumentDetailResponse:
    """Remove a user_tag (if present). Matching is case-insensitive."""
    del user_id, is_admin  # tagging is company-wide (2026-07-03)
    target = (tag or "").strip().lower()
    if not target:
        raise BadRequest("tag 不能为空")
    doc = _load_any(db, document_id)
    existing = list(doc.user_tags or [])
    filtered = [t for t in existing if t != target]
    if len(filtered) == len(existing):
        # Tag wasn't there — return the doc as-is rather than 404. Keeps
        # the API idempotent for double-clicks / network retries.
        return _serialize_detail(db, doc)
    doc.user_tags = filtered or None
    repository.save(db, doc)
    return _serialize_detail(db, doc)


def list_user_tags(
    db: Session,
    *,
    user_id: int,
    is_admin: bool,
) -> list[dict[str, Any]]:
    """Tag inventory for the filter sidebar — `[{name, count}]` sorted by name.

    `count` is how many documents carry that user_tag, so the sidebar
    can render `紧急 (3)` etc. Tags are shared company-wide (2026-07-03
    Felix decision) — anyone can see the full tag inventory.
    """
    del user_id, is_admin  # tags are company-wide
    from sqlalchemy import select

    stmt = select(Document.user_tags)
    counts: dict[str, int] = {}
    for (tags,) in db.execute(stmt):
        if not isinstance(tags, list):
            continue
        for t in tags:
            if isinstance(t, str) and t:
                counts[t] = counts.get(t, 0) + 1
    return [{"name": name, "count": counts[name]} for name in sorted(counts)]


# ═════ Delete / reextract ═══════════════════════════════════════════


def delete_document(
    db: Session,
    *,
    document_id: int,
    user_id: int,
    is_admin: bool,
    force: bool = False,
) -> dict[str, object]:
    """Delete a document + its stored file.

    Order-linkage handling (real, post-2026-05-25 — earlier code only ever
    DELETEd the document row and crashed with FK violation when an Order
    referenced it):

      • If no Order rows reference `document_id` → delete unconditionally.
      • If some Orders reference it AND `force=False` → raise BadRequest;
        caller (frontend) should re-issue with force=true after user confirms.
      • If `force=True` → set `Order.document_id = NULL` on every referencing
        Order, flush so the FK is released, THEN delete the document. The
        Orders themselves are preserved (customer expectation, encoded in the
        UI copy "订单会保留，与此源文档解除关联").

    Storage cleanup is best-effort: a failure there is logged but does NOT
    roll back the DB transaction. The DB state is the source of truth; an
    orphaned file in GCS is cheap to garbage-collect later.
    """
    # TD-1 — cross-domain lookup via orders.service so this module no
    # longer imports `Order` directly. Two-step: get IDs first (force
    # gate decision), then bulk-unlink + flush via the orders service.
    from domains.orders import service as orders_service

    doc = _load_for_user(db, document_id, user_id, is_admin)
    file_url = doc.file_url

    linked_ids = orders_service.find_order_ids_linked_to_document(db, doc.id)
    if linked_ids and not force:
        raise BadRequest(
            f"文档已关联 {len(linked_ids)} 个订单"
            f"（#{', #'.join(map(str, linked_ids))}），"
            f"请使用 force=true 强制删除（订单将保留，但与此源文档解除关联）"
        )

    unlinked_ids: list[int] = list(linked_ids)
    if unlinked_ids:
        # Releases the FK before we delete the Document row; without
        # this the subsequent repository.delete() hits the same
        # ForeignKeyViolation that the legacy code crashed on.
        orders_service.unlink_orders_from_document(db, unlinked_ids)

    repository.delete(db, doc)
    if file_url:
        try:
            get_storage().delete(file_url)
        except Exception as exc:
            logger.warning("delete: storage cleanup failed for %s: %s", file_url, exc)
    return {
        "ok": True,
        "document_id": document_id,
        "unlinked_order_ids": unlinked_ids,
        # Legacy field — kept so existing v2 frontend that reads
        # `unlinked_order_id` (singular) doesn't break. Returns the first ID
        # or None when nothing was unlinked.
        "unlinked_order_id": unlinked_ids[0] if unlinked_ids else None,
    }


def reextract(
    db: Session,
    *,
    document_id: int,
    user_id: int,
    is_admin: bool,
) -> DocumentDetailResponse:
    doc = _load_for_user(db, document_id, user_id, is_admin)
    if doc.status in ("extracting",):
        raise StatusConflict("文档正在抽取中")
    runner = get_job_runner()
    runner.submit(workflow.run_document_pipeline, doc.id)
    doc.status = "extracting"
    repository.save(db, doc)
    return _serialize_detail(db, doc)


# ═════ Order payload (Phase 2 stub) ═══════════════════════════════


def build_order_payload(
    db: Session,
    *,
    document_id: int,
    user_id: int,
    is_admin: bool,
) -> OrderPayloadResponse:
    """Phase 2 returns a placeholder payload. Phase 3 will fill it from the Order row."""
    del user_id, is_admin  # read-only, company-wide (2026-07-03)
    doc = _load_any(db, document_id)
    if doc.status in ("uploaded", "extracting"):
        raise StatusConflict("文档尚在提取中，请稍后再试")
    if doc.status == "error":
        raise BadRequest(doc.processing_error or "文档提取失败")

    return OrderPayloadResponse(
        document_id=doc.id,
        doc_type=doc.doc_type,
        order_metadata={},
        products=[],
        product_count=0,
        missing_fields=[],
        blocking_missing_fields=["not_implemented_until_phase_3"],
        field_evidence={},
        confidence_summary={
            "status": "needs_review",
            "has_products": False,
            "metadata_fields_present": 0,
            "metadata_fields_required": 0,
        },
        ready_for_order_creation=False,
    )


def apply_metadata_overrides(
    db: Session,
    *,
    document_id: int,
    user_id: int,
    is_admin: bool,
    fields: dict[str, Any],  # noqa: ARG001 (stored but not yet consumed)
) -> OrderPayloadResponse:
    """Phase 2 stub — accepts input, stores under `extracted_data['manual_overrides']`."""
    # Metadata edits stay company-wide; retain the caller for the response helper.
    doc = _load_any(db, document_id)
    if doc.status in ("uploaded", "extracting"):
        raise StatusConflict("文档尚在提取中")
    existing = dict(doc.extracted_data or {})
    existing["manual_overrides"] = {**(existing.get("manual_overrides") or {}), **fields}
    doc.extracted_data = existing
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(doc, "extracted_data")
    repository.save(db, doc)
    return build_order_payload(db, document_id=document_id, user_id=user_id, is_admin=is_admin)


# ═════ Serialization ═════════════════════════════════════════════


def _serialize(db: Session, doc: Document) -> DocumentResponse:
    uploader = _lookup_uploader(db, doc.user_id)
    return DocumentResponse(
        id=doc.id,
        user_id=doc.user_id,
        uploader_email=uploader[0] if uploader else None,
        uploader_name=uploader[1] if uploader else None,
        filename=doc.filename,
        display_name=doc.display_name,
        folder_id=doc.folder_id,
        file_url=doc.file_url,
        file_type=doc.file_type,
        file_size_bytes=doc.file_size_bytes,
        doc_type=doc.doc_type,
        extraction_method=doc.extraction_method,
        status=doc.status,
        processing_error=doc.processing_error,
        product_count=_count_products(doc),
        linked_order_id=_lookup_linked_order_id(db, doc.id),
        preview_url=_preview_url(doc),
        preview_text=_preview_text(doc),
        tags=list(doc.tags) if doc.tags else None,
        user_tags=list(doc.user_tags) if doc.user_tags else None,
        summary=doc.summary,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        extracted_at=doc.extracted_at,
    )


def _lookup_uploader(db: Session, user_id: int | None) -> tuple[str, str | None] | None:
    """Return `(email, full_name)` for the document's uploader.

    Documents are company-wide (2026-07-03) so the UI shows who uploaded
    each doc in the list + detail. Missing user (deleted account,
    orphan row) → None; the UI falls back to the numeric `user_id`.
    """
    if not user_id:
        return None
    from domains.identity import User

    u = db.get(User, user_id)
    if u is None:
        return None
    return (u.email, u.full_name)


def _lookup_linked_order_id(db: Session, document_id: int) -> int | None:
    """Cross-domain peek into the orders domain — used so the document detail
    payload carries `linked_order_id` for the frontend's "前往订单" CTA.

    Lazy import because document is a parent type and orders is a subtype:
    the orders package side-imports document for its classifier rules, so a
    top-level import here would create a cycle on cold-import paths.
    """
    from domains.orders import service as orders_service

    return orders_service.find_order_id_for_document(db, document_id)


def _serialize_detail(db: Session, doc: Document) -> DocumentDetailResponse:
    base = _serialize(db, doc)
    return DocumentDetailResponse(
        **base.model_dump(),
        content_markdown=doc.content_markdown,
        extracted_data=doc.extracted_data,
    )


def _count_products(doc: Document) -> int:
    if not doc.extracted_data:
        return 0
    # Phase 2 doesn't project products yet, so any stored legacy "products" array
    # from v2 data will show up here. Always return a number though.
    products = (doc.extracted_data or {}).get("products") or []
    return len(products) if isinstance(products, list) else 0


def _preview_url(doc: Document) -> str | None:
    if not doc.file_url:
        return None
    try:
        return get_storage().get_signed_url(doc.file_url, expires_in=3600)
    except Exception as exc:
        logger.warning("preview_url failed for %s: %s", doc.file_url, exc)
        return None


def _preview_text(doc: Document) -> str | None:
    if not doc.content_markdown:
        return None
    return doc.content_markdown.replace("\n", " ").strip()[:240] or None


# ─── Internal helpers ────────────────────────────────────────────


def _load_any(db: Session, document_id: int) -> Document:
    """Fetch a document without checking ownership.

    Used by every read + safe-integration op (get, rename, tag, move,
    metadata override). Documents are treated as company-wide assets
    (2026-07-03 Felix decision) — any logged-in user can look at any
    doc + tidy up its filing. Destructive ops go through
    `_load_for_uploader` instead.
    """
    doc = repository.get(db, document_id)
    if doc is None:
        raise NotFound("文档不存在")
    return doc


def _load_for_uploader(
    db: Session, document_id: int, user_id: int, is_admin: bool
) -> Document:
    """Fetch a document only if the caller uploaded it (or is admin).

    Destructive / expensive ops route through this: delete, doc_type
    change (triggers Gemini enrichment $), reextract (also $). Keeps
    peer employees from wrecking or driving up cost on each other's
    uploads. superadmin/admin bypass — company owner always has last say.
    """
    doc = repository.get(db, document_id)
    if doc is None:
        raise NotFound("文档不存在")
    if not is_admin and doc.user_id != user_id:
        raise NotFound("文档不存在")
    return doc


# Backward-compat alias — old external code that imported the private
# name still works. New code should use one of the two above.
_load_for_user = _load_for_uploader


# `_detect_file_type` and `_default_content_type` were moved to
# `domains.document.file_types` (single source of truth shared with
# the HTTP download handler and the workflow MIME map).
