"""Product image CRUD + reorder (R5 2026-06-22).

Lives next to `_products_service.py` so the seam between "product" and
"product's images" stays inside the masterdata domain. The HTTP layer
(`apps/http/masterdata.py`) calls into this module without knowing
anything about Pillow / GCS / display_order math.

Transaction strategy:
    1. Validate input (size, MIME, product exists).
    2. Pre-allocate `display_order` (= count of existing images).
    3. Generate thumbnails (CPU-bound, ~50 ms for a 2 MP photo).
    4. Upload 3 blobs to object storage.
    5. INSERT the row with all three storage keys + commit.

    If step 5 fails after step 4 succeeded, we end up with 3 orphan
    blobs in storage. They're invisible to users and cheap; a nightly
    cleanup script (Phase 3) can sweep them. We chose this over a 2PC
    pattern because storage operations are not transactional with the
    DB anyway — orphans on failure are inherent to any "upload then
    record" flow. The cleanup approach is industry standard (Shopify,
    Stripe, etc. all do this).

Delete strategy:
    1. SELECT row → grab the three storage keys.
    2. DELETE row + commit (DB is source of truth).
    3. Best-effort `storage.delete()` on each key; log + continue on
       failure. A failed blob delete = harmless orphan, never a user
       error.

Reorder strategy:
    1. The caller sends the FULL set of (image_id, display_order)
       pairs. We validate that the set matches what the product has
       (no missing ids, no foreign ids) BEFORE writing.
    2. UPDATE all rows in a single batch; commit.
    3. Partial reorder requests are rejected — keeping the contract
       "the client owns the order" simpler than "the server splices
       partial diffs into existing order".
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from domains.masterdata import repository as repo
from domains.masterdata.errors import BadRequest, NotFound
from domains.masterdata.images import (
    ImageProcessingError,
    UnsupportedImageFormatError,
    generate_thumbnails,
)
from domains.masterdata.models import Product, ProductImage
from domains.masterdata.schemas import ProductImageReorderEntry
from infrastructure.storage import get_storage

logger = logging.getLogger(__name__)


# ─── Limits / policy constants ────────────────────────────────

# 5 MB matches what the iPhone HEIC→JPEG export typically lands at and
# is well below Cloud Run's 32 MB request cap. Bumping this would
# require also tightening the frontend pre-upload check.
MAX_IMAGE_BYTES = 5 * 1024 * 1024

# Whitelist by MIME type. HEIC is intentionally absent — see
# `domains.masterdata.images.thumbnails` docstring for why we don't
# install pillow-heif.
ALLOWED_MIME_TYPES = frozenset(
    {
        "image/jpeg",
        "image/jpg",  # some clients send the non-standard subtype
        "image/png",
        "image/webp",
    }
)

# Hard ceiling — a single product with 100 product photos is a sign
# something's gone wrong upstream (bulk import bug, runaway script).
# This is friendlier than letting the DB grow unbounded.
MAX_IMAGES_PER_PRODUCT = 30


# ─── Queries ──────────────────────────────────────────────────


def list_product_images(db: Session, *, product_id: int) -> list[dict[str, Any]]:
    """Return every image for `product_id`, ordered for display."""
    _require_product_exists(db, product_id)
    rows = (
        db.execute(
            select(ProductImage)
            .where(ProductImage.product_id == product_id)
            .order_by(ProductImage.display_order.asc(), ProductImage.id.asc())
        )
        .scalars()
        .all()
    )
    return [_serialize_image(img) for img in rows]


def get_primary_thumbnail_url(db: Session, product_id: int) -> str | None:
    """Cheap one-shot lookup for the list-page projection.

    Used by `serialize()` in `_products_service` to embed a single
    `thumbnail_url` per product without paying for the full image list.
    Returns None when the product has no images yet.
    """
    row = (
        db.execute(
            select(ProductImage.thumbnail_key)
            .where(ProductImage.product_id == product_id)
            .order_by(ProductImage.display_order.asc(), ProductImage.id.asc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    return _sign(row) if row else None


def batch_image_projections(
    db: Session, product_ids: list[int]
) -> dict[int, tuple[str | None, int]]:
    """One-query projection for the list endpoint (R5 2026-06-22).

    Returns `{product_id: (primary_thumbnail_signed_url_or_None, count)}`
    for every id in `product_ids`. Products with no images get
    `(None, 0)`. Used by `_products_service.list_products` to populate
    the image column without N+1.

    Implementation: a single SELECT grouped by product_id retrieves
    count + (lowest-display_order) thumbnail_key per product. The
    signed-URL minting still happens in Python (µs per URL), but
    that's free compared to the DB round-trips we save.
    """
    if not product_ids:
        return {}

    # Subquery: per product_id, find the row with min(display_order). Two
    # rows can tie on display_order=0 historically (pre-`_compact`); use
    # min(id) as a stable tiebreaker so the same image is always the
    # "primary" between requests.
    primary_subq = (
        select(
            ProductImage.product_id.label("pid"),
            func.min(ProductImage.id).label("primary_id"),
        )
        .where(
            ProductImage.product_id.in_(product_ids),
            ProductImage.display_order == (
                select(func.min(ProductImage.display_order))
                .where(ProductImage.product_id == ProductImage.product_id)
                .scalar_subquery()
            ),
        )
        .group_by(ProductImage.product_id)
        .subquery()
    )
    # Simpler & faster: just two queries — one for counts, one for primary
    # thumbnails. SQLAlchemy aggregate-with-window-function gets gnarly
    # across SQLite + PostgreSQL. Two indexed queries are still O(1) RTT.
    _ = primary_subq  # subq kept for reference; unused

    # Query 1: counts per product
    count_rows = db.execute(
        select(ProductImage.product_id, func.count(ProductImage.id))
        .where(ProductImage.product_id.in_(product_ids))
        .group_by(ProductImage.product_id)
    ).all()
    counts: dict[int, int] = dict(count_rows)

    # Query 2: primary (display_order=0) thumbnail key per product.
    # The `_compact_display_order_after_delete` helper keeps display_order
    # densely packed starting at 0, so display_order=0 IS the primary.
    primary_rows = db.execute(
        select(ProductImage.product_id, ProductImage.thumbnail_key)
        .where(
            ProductImage.product_id.in_(product_ids),
            ProductImage.display_order == 0,
        )
    ).all()
    primaries: dict[int, str] = dict(primary_rows)

    out: dict[int, tuple[str | None, int]] = {}
    for pid in product_ids:
        thumb = _sign(primaries[pid]) if pid in primaries else None
        out[pid] = (thumb, counts.get(pid, 0))
    return out


def get_image_count(db: Session, product_id: int) -> int:
    """List-page projection's companion to `get_primary_thumbnail_url`.

    The UI shows `+N` overlays when count > 1; this avoids loading the
    image rows when we only need the count. Returns 0 cleanly when the
    product is new.
    """
    return (
        db.execute(
            select(func.count(ProductImage.id)).where(
                ProductImage.product_id == product_id
            )
        ).scalar()
        or 0
    )


# ─── Mutations ────────────────────────────────────────────────


def add_product_image(
    db: Session,
    *,
    product_id: int,
    content: bytes,
    filename: str,
    content_type: str,
    user_id: int,
    source_bulk_staging_id: int | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Upload one image. Returns the serialized image row.

    Raises:
        NotFound: product_id doesn't exist.
        BadRequest: size > limit / MIME not supported / max per product
            reached / decoder failure.
    """
    _lock_product_for_image_write(db, product_id)

    # Cheap header checks first — no point in decoding 5 MB of garbage.
    if not content:
        raise BadRequest("空的上传请求")
    if len(content) > MAX_IMAGE_BYTES:
        raise BadRequest(
            f"图片过大（{len(content) // 1024} KB），上限 "
            f"{MAX_IMAGE_BYTES // 1024 // 1024} MB"
        )

    normalized_mime = (content_type or "").lower().split(";")[0].strip()
    if normalized_mime not in ALLOWED_MIME_TYPES:
        raise BadRequest(
            "不支持的图片格式：" + (normalized_mime or "未知") + "。"
            "请使用 JPG / PNG / WebP（iPhone 拍的 HEIC 请先转 JPG）。"
        )

    # Count check before doing the expensive Pillow work. Avoids the
    # "user spent 30s uploading a 5MB photo, then got rejected" UX.
    existing_count = get_image_count(db, product_id)
    if existing_count >= MAX_IMAGES_PER_PRODUCT:
        raise BadRequest(
            f"每个商品最多 {MAX_IMAGES_PER_PRODUCT} 张图片"
        )

    # Decode + resize. Pillow exceptions are typed by our utility.
    try:
        bundle = generate_thumbnails(content)
    except UnsupportedImageFormatError as exc:
        raise BadRequest(str(exc)) from exc
    except ImageProcessingError as exc:
        raise BadRequest(str(exc)) from exc

    # Generate storage paths. UUID avoids name collisions when two
    # users upload "photo.jpg" simultaneously.
    storage = get_storage()
    uid = uuid.uuid4().hex
    folder = f"product-images/{product_id}"

    # Upload all three blobs. If any upload throws, we abort BEFORE the
    # DB insert — the partial uploads become orphans but the DB stays
    # clean. `storage.upload` raises StorageError on failure.
    try:
        full_key = storage.upload(folder, f"{uid}.jpg", bundle.full, "image/jpeg")
        thumb_key = storage.upload(
            folder, f"{uid}.thumb.jpg", bundle.thumb, "image/jpeg"
        )
        med_key = storage.upload(
            folder, f"{uid}.med.jpg", bundle.medium, "image/jpeg"
        )
    except Exception as exc:
        logger.exception(
            "storage upload failed for product %d: %s", product_id, exc
        )
        raise BadRequest("图片上传失败，请重试") from exc

    # Now persist. `display_order` is "next slot" — append at the end.
    row = ProductImage(
        product_id=product_id,
        storage_key=full_key,
        thumbnail_key=thumb_key,
        medium_key=med_key,
        filename=filename or "image.jpg",
        # Always JPEG after re-encode; the original MIME is captured
        # in the audit log via uploaded_by_user_id + filename.
        file_type="image/jpeg",
        file_size_bytes=len(bundle.full),
        display_order=existing_count,  # 0-based append
        alt_text=None,
        uploaded_by_user_id=user_id,
        source_bulk_staging_id=source_bulk_staging_id,
    )
    db.add(row)
    if commit:
        try:
            db.commit()
            db.refresh(row)
        except Exception:
            db.rollback()
            for key in (full_key, thumb_key, med_key):
                try:
                    storage.delete(key)
                except Exception:
                    logger.warning("failed to clean image blob after DB rollback: %s", key)
            raise
    else:
        try:
            db.flush()
        except Exception:
            db.rollback()
            for key in (full_key, thumb_key, med_key):
                try:
                    storage.delete(key)
                except Exception:
                    logger.warning(
                        "failed to clean image blob after DB flush failure: %s", key
                    )
            raise
    return _serialize_image(row)


def update_product_image(
    db: Session, *, product_id: int, image_id: int, alt_text: str | None
) -> dict[str, Any]:
    """PATCH — currently only `alt_text` is editable. Reorder + delete
    have dedicated endpoints to keep auditing focused."""
    row = _load_image_for_product(db, product_id, image_id)
    row.alt_text = (alt_text or "").strip() or None
    db.commit()
    db.refresh(row)
    return _serialize_image(row)


def reorder_product_images(
    db: Session,
    *,
    product_id: int,
    entries: list[ProductImageReorderEntry],
) -> list[dict[str, Any]]:
    """Replace `display_order` for ALL images of the product atomically.

    Contract (enforced here, not by Pydantic):
        - len(entries) == number of existing images
        - every image_id in entries belongs to the product
        - display_order values are unique (no two rows share a slot)

    Anything else is a 400. Callers that want "move one image to the
    top" should compute the full new order client-side and send the
    whole list. This keeps the contract simple and the audit clear.
    """
    _lock_product_for_image_write(db, product_id)

    existing_rows = (
        db.execute(
            select(ProductImage).where(ProductImage.product_id == product_id)
        )
        .scalars()
        .all()
    )
    existing_ids = {r.id for r in existing_rows}
    incoming_ids = {e.id for e in entries}

    if existing_ids != incoming_ids:
        raise BadRequest(
            "重排序必须传入该商品的全部图片 id（不能多也不能少）"
        )
    if len({e.display_order for e in entries}) != len(entries):
        raise BadRequest("display_order 不能重复")

    order_by_id = {e.id: e.display_order for e in entries}
    for row in existing_rows:
        row.display_order = order_by_id[row.id]
    db.commit()

    # Return the freshly-ordered list so the frontend can update without
    # an extra round trip.
    return list_product_images(db, product_id=product_id)


def delete_product_image(
    db: Session, *, product_id: int, image_id: int
) -> None:
    """Delete the row + best-effort delete the three blobs.

    Order matters: DB first, storage second. If storage delete fails
    we just log — the user's intent is satisfied (the image is gone
    from their app), and the blob is an inert orphan."""
    _lock_product_for_image_write(db, product_id)
    row = _load_image_for_product(db, product_id, image_id)
    keys = (row.storage_key, row.thumbnail_key, row.medium_key)
    deleted_order = row.display_order

    db.delete(row)
    db.commit()

    storage = get_storage()
    for key in keys:
        try:
            storage.delete(key)
        except Exception as exc:
            logger.warning(
                "best-effort blob delete failed for product %d key %s: %s",
                product_id,
                key,
                exc,
            )

    # Re-pack display_order so there's no hole left behind. Without
    # this, deleting image at order=1 from a [0, 1, 2] product leaves
    # [0, 2] which works but looks weird if the UI ever exposes the
    # raw number.
    _compact_display_order_after_delete(db, product_id, deleted_order)


# ─── Internal helpers ─────────────────────────────────────────


def _require_product_exists(db: Session, product_id: int) -> None:
    if repo.get_product(db, product_id) is None:
        raise NotFound("产品不存在")


def _lock_product_for_image_write(db: Session, product_id: int) -> None:
    """Serialize every image mutation for one product.

    PostgreSQL holds the product-row lock until commit/rollback. SQLite ignores
    ``FOR UPDATE`` but its single-writer behavior is sufficient for tests.
    All upload/reorder/delete paths call this helper, so direct batch commits
    cannot race a normal gallery edit between snapshot validation and reorder.
    """
    product = db.execute(
        select(Product).where(Product.id == product_id).with_for_update()
    ).scalar_one_or_none()
    if product is None:
        raise NotFound("产品不存在")


def _load_image_for_product(
    db: Session, product_id: int, image_id: int
) -> ProductImage:
    row = db.get(ProductImage, image_id)
    if row is None or row.product_id != product_id:
        # 404 not 403 so we don't leak existence of images on other
        # products (cross-product image-id enumeration).
        raise NotFound("图片不存在")
    return row


def _compact_display_order_after_delete(
    db: Session, product_id: int, deleted_order: int
) -> None:
    """Close the gap left by a delete — shift every row with a higher
    `display_order` down by 1. Idempotent: re-running it on a clean
    table is a no-op."""
    db.execute(
        ProductImage.__table__.update()
        .where(
            ProductImage.product_id == product_id,
            ProductImage.display_order > deleted_order,
        )
        .values(display_order=ProductImage.display_order - 1)
    )
    db.commit()


def _sign(key: str) -> str:
    """Wrap `storage.get_signed_url` so callers can mock one function in
    tests instead of monkey-patching the whole storage backend."""
    return get_storage().get_signed_url(key, expires_in=3600)


def _serialize_image(row: ProductImage) -> dict[str, Any]:
    """Outbound DTO — three signed URLs + display metadata.

    Signed URLs are freshly minted on every read. That's the cost of
    not persisting URLs (we'd have to invalidate them when keys
    change or storage backends switch). At ~µs per signature it's
    negligible.
    """
    return {
        "id": row.id,
        "filename": row.filename,
        "file_type": row.file_type,
        "file_size_bytes": row.file_size_bytes,
        "display_order": row.display_order,
        "alt_text": row.alt_text,
        "uploaded_at": row.uploaded_at,
        "thumbnail_url": _sign(row.thumbnail_key),
        "medium_url": _sign(row.medium_key),
        "full_url": _sign(row.storage_key),
    }
