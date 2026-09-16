"""Service layer for bulk image upload — DB-aware orchestration on top of
the pure ZIP functions in `bulk_upload.py`.

Surfaces:
    list_template_products(...)     — feeds the template-download endpoint
    create_preview(...)             — parses ZIP + stages rows; called sync from HTTP
    list_batch(...)                 — read-only detail for the polling UI
    commit_batch(...)               — background job entry; ingests via add_product_image
    cancel_batch(...)               — user backs out; cleans up staging + ZIP

Status-machine contract (locks against double-commit, see migration 0021):
    uploading → preview_ready → processing → completed
                              \\> cancelled
                              \\> error

Anything that mutates `status` does it via the DB row's optimistic
status check pattern: read current → assert allowed → write new. No
in-memory caching of batch state — the DB is the single source of
truth, which is what lets the background job (running in a fresh
SessionLocal) coexist with the HTTP polling reader.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from domains.masterdata import _product_images_service as image_service
from domains.masterdata.images.bulk_models import BulkImageBatch, BulkImageStaging
from domains.masterdata.images.bulk_upload import (
    ParsedEntry,
    TemplateProductRow,
    build_template_zip,
    parse_uploaded_zip,
)
from domains.masterdata.models import Country, Port, Product, ProductImage
from infrastructure.storage import get_storage

logger = logging.getLogger(__name__)


# ─── Errors ──────────────────────────────────────────────────


class BulkImageError(Exception):
    pass


class NotFound(BulkImageError):
    pass


class BadRequest(BulkImageError):
    pass


class StatusConflict(BulkImageError):
    """Operation not valid for the batch's current status."""


# ─── Template generation ─────────────────────────────────────


def build_template(
    db: Session,
    *,
    country_ids: list[int] | None = None,
    port_ids: list[int] | None = None,
    only_missing_images: bool = False,
) -> bytes:
    """Generate the template ZIP for the given filter.

    Filters compose with AND. `only_missing_images=True` further
    restricts to products that currently have zero ProductImage rows —
    common case is "I just want to补图 the new arrivals".

    Returns the ZIP bytes; HTTP layer streams them to the user.
    """
    # Sub-query: product_ids that have at least one image.
    if only_missing_images:
        with_images_sq = (
            select(ProductImage.product_id).distinct().scalar_subquery()
        )
    else:
        with_images_sq = None

    stmt = (
        select(
            Product.code,
            Product.product_name_en.label("product_name"),
            Country.name.label("country_name"),
            Port.name.label("port_name"),
            # Coalesce the image-exists flag here so the template
            # function only needs a boolean. The exists() subquery is
            # bounded — one boolean per product, no scan blow-up.
            func.coalesce(
                select(func.count(ProductImage.id))
                .where(ProductImage.product_id == Product.id)
                .correlate(Product)
                .scalar_subquery()
                > 0,
                False,
            ).label("has_images"),
        )
        .join(Country, Country.id == Product.country_id)
        .join(Port, Port.id == Product.port_id)
        .where(Product.code.is_not(None))
        .where(Product.code != "")
    )

    if country_ids:
        stmt = stmt.where(Product.country_id.in_(country_ids))
    if port_ids:
        stmt = stmt.where(Product.port_id.in_(port_ids))
    if with_images_sq is not None:
        stmt = stmt.where(Product.id.not_in(with_images_sq))

    # Deterministic ordering — users browsing in Finder/Explorer get a
    # predictable layout. Country → port → code lexical.
    stmt = stmt.order_by(Country.name, Port.name, Product.code)

    rows: list[TemplateProductRow] = []
    for code, product_name, country_name, port_name, has_images in db.execute(stmt).all():
        rows.append(
            TemplateProductRow(
                country_name=country_name or "Unknown",
                port_name=port_name or "Unknown",
                product_code=code,
                product_name=product_name or "",
                has_images=bool(has_images),
            )
        )

    return build_template_zip(rows)


# ─── Preview (parse + stage, NO ingestion) ──────────────────


def create_preview(
    db: Session,
    *,
    user_id: int,
    zip_filename: str,
    zip_bytes: bytes,
) -> BulkImageBatch:
    """Parse the uploaded ZIP, store it in GCS, write staging rows.

    Does NOT mutate `products` or `v3_product_images`. The user reviews
    the preview, then calls commit_batch separately.

    Returns the persisted `BulkImageBatch` (status=preview_ready or
    status=error if parsing failed hard).
    """
    if not zip_filename:
        raise BadRequest("ZIP 文件名不能为空")
    if not zip_bytes:
        raise BadRequest("ZIP 内容不能为空")

    # Single-active-batch-per-user guard (2026-07-03): commit is async
    # and the UI can only track ONE batch's progress at a time. Two
    # simultaneous batches for the same user would race each other on
    # MAX_IMAGES_PER_PRODUCT limits and would confuse the polling UI.
    # Tell the user to cancel or finish the old one first.
    existing = db.execute(
        select(BulkImageBatch.id, BulkImageBatch.status)
        .where(BulkImageBatch.user_id == user_id)
        .where(BulkImageBatch.status.in_(("uploading", "preview_ready", "processing")))
        .order_by(BulkImageBatch.id.desc())
        .limit(1)
    ).first()
    if existing is not None:
        raise BadRequest(
            f"你已有一个未完成的批次（#{existing[0]}，状态={existing[1]}）。"
            "请先提交或取消它再上传新 ZIP。"
        )

    # Create the batch envelope first so partial failures are visible.
    batch = BulkImageBatch(
        user_id=user_id,
        zip_filename=zip_filename,
        status="uploading",
    )
    db.add(batch)
    db.flush()  # gets batch.id without committing

    # Stash the raw ZIP in object storage so the background commit job
    # doesn't need it in DB. Naming convention: `bulk-image-staging/{id}.zip`.
    storage = get_storage()
    storage_key = storage.upload(
        "bulk-image-staging",
        f"batch-{batch.id}-{uuid.uuid4().hex}.zip",
        zip_bytes,
        content_type="application/zip",
    )
    batch.zip_storage_key = storage_key

    # Parse — failures here are hard fatal (corrupt ZIP); soft per-row
    # failures (bad ext / oversize) become error-status staging rows.
    try:
        entries, warnings = parse_uploaded_zip(zip_bytes)
    except ValueError as exc:
        batch.status = "error"
        batch.error_message = str(exc)
        db.commit()
        return batch

    if warnings:
        # We don't fail the batch over warnings, but surface them in
        # the error_message field so the UI can show them.
        batch.error_message = " / ".join(warnings)[:1000]

    # Resolve identities. We do one bulk lookup per (country, port,
    # code) triple — N+1 would be brutal at ~500 rows. Build the lookup
    # table once.
    triples = {(e.country_name, e.port_name, e.product_code) for e in entries}
    resolved = _resolve_product_ids(db, triples) if triples else {}

    counters = {"matched": 0, "unmatched": 0, "error": 0}
    for entry in entries:
        staging = _build_staging_row(batch.id, entry, resolved)
        db.add(staging)
        counters[staging.status if staging.status in counters else "error"] += 1

    batch.total_files = len(entries)
    batch.matched_count = counters["matched"]
    batch.unmatched_count = counters["unmatched"]
    batch.error_count = counters["error"]
    batch.status = "preview_ready"
    db.commit()
    db.refresh(batch)
    return batch


def _resolve_product_ids(
    db: Session, triples: set[tuple[str, str, str]]
) -> dict[tuple[str, str, str], int]:
    """Map (country_name, port_name, product_code) → product_id.

    Performs a single multi-key lookup query keyed by case-insensitive
    name comparisons. When a triple has multiple matching products
    (e.g. legacy duplicates), we pick the one with the latest
    `effective_to` (NULL = open-ended, treated as "latest") — same
    tiebreaker as the matcher uses elsewhere.
    """
    if not triples:
        return {}

    # Collect unique country / port names so the SQL `IN` clause stays
    # bounded even if the user has many triples sharing names.
    country_names = {t[0].lower() for t in triples if t[0]}
    port_names = {t[1].lower() for t in triples if t[1]}
    codes = {t[2] for t in triples if t[2]}

    if not (country_names and port_names and codes):
        return {}

    stmt = (
        select(
            Product.id,
            Product.code,
            func.lower(Country.name).label("country_l"),
            func.lower(Port.name).label("port_l"),
            Product.effective_to,
        )
        .join(Country, Country.id == Product.country_id)
        .join(Port, Port.id == Product.port_id)
        .where(func.lower(Country.name).in_(country_names))
        .where(func.lower(Port.name).in_(port_names))
        .where(Product.code.in_(codes))
    )

    candidates: dict[tuple[str, str, str], tuple[int, str | None]] = {}
    for pid, code, country_l, port_l, eff_to in db.execute(stmt).all():
        # Find the triple this row maps to. Since we lower-cased the
        # query side, we lower-case the lookup side too.
        for t in triples:
            if (
                t[0].lower() == country_l
                and t[1].lower() == port_l
                and t[2] == code
            ):
                prev = candidates.get(t)
                # Tiebreaker: prefer NULL effective_to (open-ended), then
                # newest effective_to. eff_to is stored as String here per
                # the model — lexicographic comparison works because writes
                # are normalized to YYYY-MM-DD.
                if (
                    prev is None
                    or (eff_to is None and prev[1] is not None)
                    or (
                        eff_to is not None
                        and prev[1] is not None
                        and eff_to > prev[1]
                    )
                ):
                    candidates[t] = (pid, eff_to)
                break

    return {t: pid for t, (pid, _) in candidates.items()}


def _build_staging_row(
    batch_id: int,
    entry: ParsedEntry,
    resolved: dict[tuple[str, str, str], int],
) -> BulkImageStaging:
    """Translate a parsed ZIP entry into a staging row with status."""
    triple = (entry.country_name, entry.port_name, entry.product_code)
    product_id = resolved.get(triple)

    if entry.error:
        status = "error"
        err = entry.error
    elif product_id is None:
        status = "unmatched"
        err = f"未在 ({entry.country_name}, {entry.port_name}) 下找到产品代码 {entry.product_code!r}"
    else:
        status = "matched"
        err = None

    return BulkImageStaging(
        batch_id=batch_id,
        zip_path=entry.zip_path,
        country_name=entry.country_name,
        port_name=entry.port_name,
        product_code=entry.product_code,
        image_filename=entry.image_filename,
        file_size_bytes=len(entry.content),
        product_id=product_id,
        status=status,
        error_message=err,
    )


# ─── Detail / cancel ─────────────────────────────────────────


def list_batch(db: Session, *, batch_id: int, user_id: int, is_admin: bool) -> dict[str, Any]:
    """Return the batch envelope + grouped staging rows for the UI.

    Limited to 500 rows surfaced inline (matched + unmatched + error
    combined) — beyond that, the UI gets a "showing first 500, X more
    omitted" notice. Real ZIPs hit the 100MB cap long before this.
    """
    batch = _load_batch(db, batch_id, user_id, is_admin)
    rows = db.execute(
        select(BulkImageStaging)
        .where(BulkImageStaging.batch_id == batch.id)
        .order_by(BulkImageStaging.id)
        .limit(500)
    ).scalars().all()
    return {
        "id": batch.id,
        "zip_filename": batch.zip_filename,
        "status": batch.status,
        "total_files": batch.total_files,
        "matched_count": batch.matched_count,
        "unmatched_count": batch.unmatched_count,
        "error_count": batch.error_count,
        "ingested_count": batch.ingested_count,
        "error_message": batch.error_message,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "completed_at": batch.completed_at.isoformat() if batch.completed_at else None,
        "rows": [_serialize_staging(r) for r in rows],
    }


def cancel_batch(
    db: Session,
    *,
    batch_id: int,
    user_id: int,
    is_admin: bool,
    force: bool = False,
) -> None:
    """User backed out before commit. Tears down staging + ZIP storage.

    Normal case: `preview_ready` / `uploading` / `error` batches can
    be cancelled without ceremony — nothing durable was written to
    `v3_product_images` yet.

    `force=True` (2026-07-03) also allows cancelling a `processing`
    batch. This is the manual recovery path when the background worker
    died mid-run and left the batch stuck in `processing` — the UI
    exposes it after a 15-minute polling timeout. Data written so far
    stays (ProductImage rows already added are real), but the batch
    envelope is closed so the user can start a new upload.
    """
    batch = _load_batch(db, batch_id, user_id, is_admin)
    allowed = ("preview_ready", "uploading", "error")
    if force:
        allowed = allowed + ("processing",)
    if batch.status not in allowed:
        raise StatusConflict(f"无法取消处于 {batch.status} 状态的批次")
    if _cleanup_storage_key(batch.zip_storage_key):
        batch.zip_storage_key = None
    batch.status = "cancelled"
    db.commit()


# ─── Stale batch sweeper (GC cron target) ────────────────────


def sweep_stale_batches(
    db: Session,
    *,
    ttl_hours: int = 24,
    stuck_minutes: int = 15,
    dry_run: bool = False,
) -> dict[str, int]:
    """Idempotent sweep called by the internal `/bulk-images/gc` cron.

    Two failure modes we clean up:

    1. **Abandoned** batches (`preview_ready` / `uploading` /
       `error` / `cancelled`) whose `created_at` is older than
       `ttl_hours`. Users clicked away without committing; the ZIP
       has been sitting in GCS. We close the batch (`status=cancelled`)
       and best-effort delete the ZIP.

    2. **Stuck** batches (`processing`) whose `updated_at` heartbeat
       is older than `stuck_minutes`. The background worker died —
       likely process restart / CPU throttled to death / uncaught
       exception. We fail the batch (`status=error`) so the user can
       start a new upload; already-committed images stay (they're
       real ProductImage rows).

    Cleanup of the ZIP itself is doubly-guarded:
        - The GCS bucket has a 48h prefix TTL as a hard failsafe.
        - If our DELETE call fails, we leave `zip_storage_key` set
          so the next sweep retries.

    `dry_run=True` counts what WOULD happen without mutating anything.
    """
    now = datetime.utcnow()
    stale_cutoff = now - timedelta(hours=ttl_hours)
    stuck_cutoff = now - timedelta(minutes=stuck_minutes)

    stale_stmt = (
        select(BulkImageBatch)
        .where(
            BulkImageBatch.status.in_(
                ("preview_ready", "uploading", "error", "cancelled")
            )
        )
        .where(BulkImageBatch.created_at < stale_cutoff)
        # Only rows whose ZIP is still around — cancelled/error batches
        # that already had their key cleared don't need re-work.
        .where(BulkImageBatch.zip_storage_key.is_not(None))
    )
    stuck_stmt = (
        select(BulkImageBatch)
        .where(BulkImageBatch.status == "processing")
        # NULL updated_at is treated as "unknown when it last moved"
        # → we do NOT auto-fail those (safer; user can force-cancel
        # via UI if they need to).
        .where(BulkImageBatch.updated_at.is_not(None))
        .where(BulkImageBatch.updated_at < stuck_cutoff)
    )

    stale_batches = list(db.execute(stale_stmt).scalars().all())
    stuck_batches = list(db.execute(stuck_stmt).scalars().all())

    result = {
        "stale_batches": len(stale_batches),
        "stuck_batches": len(stuck_batches),
        "storage_deleted": 0,
        "storage_delete_failed": 0,
        "dry_run": 1 if dry_run else 0,
    }
    if dry_run:
        return result

    for b in stale_batches:
        if _cleanup_storage_key(b.zip_storage_key):
            b.zip_storage_key = None
            result["storage_deleted"] += 1
        else:
            result["storage_delete_failed"] += 1
        # Keep whatever terminal-ish status the batch already had if
        # it was `cancelled` — those we're only sweeping to clean up
        # the ZIP. `preview_ready` / `uploading` / `error` we mark
        # `cancelled` so subsequent GET /batch/{id} clearly shows
        # "abandoned by GC".
        if b.status != "cancelled":
            b.status = "cancelled"
            b.error_message = (
                b.error_message or ""
            ) + f" [GC {now.isoformat()}: abandoned after {ttl_hours}h]"
    for b in stuck_batches:
        if _cleanup_storage_key(b.zip_storage_key):
            b.zip_storage_key = None
            result["storage_deleted"] += 1
        else:
            result["storage_delete_failed"] += 1
        b.status = "error"
        b.error_message = (
            f"任务卡住超过 {stuck_minutes} 分钟无进度，GC 自动标记失败。"
            "请重新上传。"
        )
    db.commit()
    return result


# ─── Commit (background job entry) ──────────────────────────


def trigger_commit(
    db: Session, *, batch_id: int, user_id: int, is_admin: bool
) -> BulkImageBatch:
    """Synchronously transition status to `processing` and return.

    The caller schedules `_run_commit_async(batch_id)` on the
    AsyncioRunner. We do the status flip + return in the request path
    so the UI's first poll sees the new state immediately, regardless
    of when the background job actually starts executing.
    """
    batch = _load_batch(db, batch_id, user_id, is_admin)
    if batch.status != "preview_ready":
        raise StatusConflict(
            f"只能提交 preview_ready 状态的批次（当前: {batch.status}）"
        )
    batch.status = "processing"
    batch.error_message = None
    db.commit()
    db.refresh(batch)
    return batch


async def _run_commit_async(batch_id: int) -> None:
    """Background job: iterate staging rows, call `add_product_image`
    for each `status=matched` row, write per-row outcomes back.

    Opens its own SessionLocal because the HTTP request's session is
    already closed by the time this runs (mirrors the orders.service
    async-commit pattern).
    """
    await asyncio.to_thread(_run_commit_sync, batch_id)


def _run_commit_sync(batch_id: int) -> None:
    from infrastructure.db import session as _session_module

    with _session_module.SessionLocal() as db:
        batch = db.get(BulkImageBatch, batch_id)
        if batch is None:
            logger.warning("bulk-image commit: batch %s not found", batch_id)
            return
        if batch.status != "processing":
            # Cancelled / already done — bail out silently. This is the
            # race-condition guard against double-commit.
            logger.info(
                "bulk-image commit: batch %s status=%s, skipping",
                batch_id,
                batch.status,
            )
            return

        rows = db.execute(
            select(BulkImageStaging)
            .where(BulkImageStaging.batch_id == batch_id)
            .where(BulkImageStaging.status == "matched")
            .order_by(BulkImageStaging.id)
        ).scalars().all()

        # We need the ZIP again to read each image's bytes — they're
        # too big to keep in DB.
        try:
            zip_bytes = get_storage().download(batch.zip_storage_key) if batch.zip_storage_key else b""
        except Exception as exc:
            logger.exception("bulk-image commit: failed to download staging ZIP")
            batch.status = "error"
            batch.error_message = f"无法下载暂存的 ZIP：{exc}"
            db.commit()
            return

        if not zip_bytes:
            batch.status = "error"
            batch.error_message = "暂存的 ZIP 已被清理，请重新上传"
            db.commit()
            return

        # Re-parse so the bytes per file are readily available. The
        # parse is cheap (we already did it once at preview); doing it
        # again here avoids needing a giant staging-bytes column.
        entries, _warnings = parse_uploaded_zip(zip_bytes)
        by_path: dict[str, bytes] = {e.zip_path: e.content for e in entries}

        ingested = 0
        for idx, row in enumerate(rows):
            content = by_path.get(row.zip_path)
            if not content:
                row.status = "committed_failed"
                row.error_message = "在 ZIP 中找不到对应文件"
            else:
                try:
                    image_service.add_product_image(
                        db,
                        product_id=row.product_id,
                        content=content,
                        filename=row.image_filename,
                        content_type=_guess_content_type(row.image_filename),
                        user_id=batch.user_id,
                    )
                    row.status = "committed"
                    ingested += 1
                except Exception as exc:
                    logger.exception(
                        "bulk-image commit: row %s failed for product %s",
                        row.id,
                        row.product_id,
                    )
                    row.status = "committed_failed"
                    row.error_message = str(exc)[:500]
            # Persist per-row outcome immediately so a mid-run crash
            # only loses the CURRENT row's progress, and the polling
            # UI sees per-row updates. batch.ingested_count + the
            # heartbeat (batch.updated_at via onupdate) are refreshed
            # every 5 rows to cut fsync-per-row overhead ~5x without
            # blinding the progress bar.
            if idx % 5 == 0 or idx == len(rows) - 1:
                batch.ingested_count = ingested
            db.commit()

        batch.status = "completed"
        batch.completed_at = datetime.utcnow()
        db.commit()

        # Cleanup ordering matters (2026-07-03 fix): delete GCS FIRST,
        # then clear the DB key. If GCS delete fails we leave the key
        # set so the next GC sweep can retry. Old code cleared the key
        # unconditionally, orphaning any blob whose delete failed.
        if batch.zip_storage_key:
            if _cleanup_storage_key(batch.zip_storage_key):
                batch.zip_storage_key = None
                db.commit()


# ─── Internal helpers ───────────────────────────────────────


def _load_batch(db: Session, batch_id: int, user_id: int, is_admin: bool) -> BulkImageBatch:
    batch = db.get(BulkImageBatch, batch_id)
    if batch is None:
        raise NotFound("批次不存在")
    if not is_admin and batch.user_id != user_id:
        # Ownership check — non-admins can only see their own uploads.
        raise NotFound("批次不存在")
    return batch


def _serialize_staging(row: BulkImageStaging) -> dict[str, Any]:
    return {
        "id": row.id,
        "zip_path": row.zip_path,
        "country_name": row.country_name,
        "port_name": row.port_name,
        "product_code": row.product_code,
        "image_filename": row.image_filename,
        "file_size_bytes": row.file_size_bytes,
        "product_id": row.product_id,
        "status": row.status,
        "error_message": row.error_message,
    }


def _cleanup_storage_key(key: str | None) -> bool:
    """Delete the staging ZIP from object storage.

    Returns True on success (or when there was nothing to delete),
    False when the storage call raised. Callers use the return value
    to decide whether to clear `batch.zip_storage_key` — leaving the
    key set on failure lets the periodic GC sweep retry later, and
    the GCS lifecycle policy (48h prefix TTL) provides a final safety
    net so nothing accumulates forever.
    """
    if not key:
        return True
    try:
        get_storage().delete(key)
        return True
    except Exception as exc:
        logger.warning("bulk-image cleanup: failed to delete %s: %s", key, exc)
        return False


def _guess_content_type(filename: str) -> str:
    name = filename.lower()
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"
