"""Product CRUD — implementation for `service.py`."""

from __future__ import annotations

from datetime import date, datetime
from functools import wraps
from typing import Any

from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from domains.masterdata import _validation, price_history
from domains.masterdata import repository as repo
from domains.masterdata.errors import BadRequest, Conflict
from domains.masterdata.models import Product
from domains.masterdata.price_periods import periods_by_product, sync_compatibility_periods
from domains.masterdata.schemas import ProductCreate, ProductUpdate
from infrastructure.storage import get_storage

_DATE_FIELDS = (
    "effective_from",
    "effective_to",
    "purchase_price_effective_from",
    "purchase_price_effective_to",
    "selling_price_effective_from",
    "selling_price_effective_to",
)
_PRICE_PERIODS = (
    ("purchase_price_effective_from", "purchase_price_effective_to", "采购价"),
    ("selling_price_effective_from", "selling_price_effective_to", "卖价"),
)


def _validate_price_periods(values: dict[str, datetime | None]) -> None:
    for start_field, end_field, label in _PRICE_PERIODS:
        start = values.get(start_field)
        end = values.get(end_field)
        if start is not None and end is not None and start > end:
            raise BadRequest(f"{label}有效开始日期不能晚于结束日期")


def _is_effective(p: Product, *, today: date | None = None) -> bool:
    """Computed availability flag (v43+).

    `Product.status` is a manual on/off switch (admin set). `is_effective`
    is the user-visible "is this product actually usable today" answer:
    requires the manual switch AND (no expiry date OR expiry not yet
    reached). `today` is parameterised so unit tests can pin time.

    The UI's StatusBadge renders off this rather than `status` so that
    products past `effective_to` automatically appear as 无效 even
    though no cron has flipped `status`. Pre-v43 we had no such
    behaviour at all — expired rows still showed as 有效.
    """
    if not p.status:
        return False
    if p.effective_to is None:
        return True
    cutoff = today or date.today()
    end = p.effective_to
    if isinstance(end, datetime):
        end = end.date()
    return end >= cutoff


def list_products(
    db: Session,
    *,
    search: str | None = None,
    country_id: int | None = None,
    port_id: int | None = None,
    category_id: int | None = None,
    supplier_id: int | None = None,
    is_effective: bool | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    total, products = repo.list_products(
        db,
        search=search,
        country_id=country_id,
        port_id=port_id,
        category_id=category_id,
        supplier_id=supplier_id,
        is_effective=is_effective,
        limit=limit,
        offset=offset,
    )
    # R5 (2026-06-22): precompute the image projection in ONE query per
    # page rather than 2 per product (N+1 = 100 queries for a 50-row
    # page). `_batch_image_projections` returns
    # `{product_id: (thumbnail_url_or_None, count)}`.
    from domains.masterdata._product_images_service import (
        batch_image_projections,
    )

    image_map = batch_image_projections(db, [p.id for p in products])
    period_map = periods_by_product(db, [p.id for p in products])
    return {
        "total": total,
        "items": [
            serialize(
                db,
                p,
                image_projection=image_map.get(p.id),
                price_period_projection=period_map.get(p.id, []),
            )
            for p in products
        ],
    }


def list_image_upload_products(
    db: Session,
    *,
    search: str | None = None,
    country_id: int | None = None,
    port_id: int | None = None,
    only_without_images: bool = False,
    limit: int = 30,
    offset: int = 0,
) -> dict[str, Any]:
    """Lean product rows for the image-upload picker, without price payloads."""
    total, rows = repo.list_image_upload_products(
        db,
        search=search,
        country_id=country_id,
        port_id=port_id,
        only_without_images=only_without_images,
        limit=limit,
        offset=offset,
    )
    storage = get_storage()
    items = []
    for row in rows:
        thumbnail_key = row.pop("thumbnail_key")
        row["thumbnail_url"] = (
            storage.get_signed_url(thumbnail_key, expires_in=3600)
            if thumbnail_key
            else None
        )
        items.append(row)
    return {"total": total, "items": items}


def _atomic_write(fn):
    @wraps(fn)
    def wrapped(db, *args, **kwargs):
        try:
            return fn(db, *args, **kwargs)
        except StaleDataError as exc:
            db.rollback()
            raise Conflict("产品已被其他操作修改，请刷新后重试") from exc
        except IntegrityError as exc:
            db.rollback()
            raise Conflict("产品身份或版本冲突，请刷新并检查重复产品") from exc
        except Exception:
            db.rollback()
            raise
    return wrapped


@_atomic_write
def create_product(db: Session, body: ProductCreate, *, actor_id: int | None = None,
                   source: str = "internal") -> dict[str, Any]:
    _validation.require_country(db, body.country_id)
    _validation.require_category(db, body.category_id)
    _validation.require_supplier(db, body.supplier_id)
    _validation.require_port(db, body.port_id)

    dates = {field: _validation.parse_date_str(getattr(body, field)) for field in _DATE_FIELDS}
    _validate_price_periods(dates)
    obj = Product(
        product_name_en=body.product_name_en,
        product_name_jp=body.product_name_jp,
        code=body.code,
        country_id=body.country_id,
        category_id=body.category_id,
        supplier_id=body.supplier_id,
        port_id=body.port_id,
        unit=body.unit,
        price=body.price,
        contract_price=body.contract_price,
        purchase_price_effective_from=dates["purchase_price_effective_from"],
        purchase_price_effective_to=dates["purchase_price_effective_to"],
        selling_price_effective_from=dates["selling_price_effective_from"],
        selling_price_effective_to=dates["selling_price_effective_to"],
        unit_size=body.unit_size,
        pack_size=body.pack_size,
        country_of_origin=body.country_of_origin,
        brand=body.brand,
        currency=body.currency,
        effective_from=dates["effective_from"],
        effective_to=dates["effective_to"],
        status=body.status,
    )
    db.add(obj)
    price_history.initial(db, obj, actor_id=actor_id, source=source)
    sync_compatibility_periods(
        db, obj, actor_id=actor_id, source=source
    )
    _commit_or_translate(db)
    db.refresh(obj)
    return serialize(db, obj)


@_atomic_write
def update_product(db: Session, product_id: int, body: ProductUpdate, *,
                   actor_id: int | None = None, source: str = "internal",
                   event_type: str = "change", restores_id: str | None = None) -> dict[str, Any]:
    obj = price_history.lock_product(db, product_id, body.expected_revision)
    before = price_history.snapshot(obj)
    data = body.model_dump(exclude_unset=True, exclude={"expected_revision"})

    if "country_id" in data:
        _validation.require_country(db, data["country_id"])
    if "category_id" in data:
        _validation.require_category(db, data["category_id"])
    if "supplier_id" in data:
        _validation.require_supplier(db, data["supplier_id"])
    if "port_id" in data:
        _validation.require_port(db, data["port_id"])

    for date_field in _DATE_FIELDS:
        if date_field in data:
            data[date_field] = _validation.parse_date_str(data[date_field])

    prospective_dates = {
        field: data[field] if field in data else getattr(obj, field)
        for field in _DATE_FIELDS
    }
    _validate_price_periods(prospective_dates)

    for k, v in data.items():
        setattr(obj, k, v)
    sync_compatibility_periods(
        db, obj, actor_id=actor_id, source=source
    )
    price_history.record_change(db, obj, before, actor_id=actor_id, source=source,
                                event_type=event_type, restores_id=restores_id)
    _commit_or_translate(db)
    db.refresh(obj)
    return serialize(db, obj)


@_atomic_write
def delete_product(db: Session, product_id: int, *, actor_id: int | None = None,
                   source: str = "internal", expected_revision: int | None = None) -> None:
    obj = price_history.lock_product(db, product_id, expected_revision)
    price_history.record_change(db, obj, price_history.snapshot(obj), actor_id=actor_id,
                                source=source, event_type="delete")
    db.flush()
    db.delete(obj)
    db.commit()


def _commit_or_translate(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("该产品在同一国家和港口下已存在") from exc
    except DataError as exc:
        db.rollback()
        raise BadRequest("数据格式错误，请检查字段长度和类型") from exc


def serialize(
    db: Session,
    p: Product,
    *,
    image_projection: tuple[str | None, int] | None = None,
    price_period_projection: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Serialize a Product to dict for the HTTP layer.

    `image_projection`, when provided, is a precomputed
    `(thumbnail_url_or_None, image_count)` pair from a batched query —
    used by `list_products` to avoid N+1 (without it we'd run 2 extra
    queries per row). When None we fall through to per-product lookups
    (for single GETs that path is fine).
    """
    if image_projection is None:
        # Lazy-import to break the import cycle: `_product_images_service`
        # imports `repo` which doesn't reach back to us, but listing as a
        # top-level import here would still drag the image module into
        # every product-service import chain (including from masterdata's
        # __init__). Cheap to import-on-demand.
        from domains.masterdata._product_images_service import (
            get_image_count,
            get_primary_thumbnail_url,
        )
        thumbnail_url = get_primary_thumbnail_url(db, p.id)
        image_count = get_image_count(db, p.id)
    else:
        thumbnail_url, image_count = image_projection
    if price_period_projection is None:
        price_period_projection = periods_by_product(db, [p.id]).get(p.id, [])

    names = repo.lookup_names(
        db,
        country_id=p.country_id,
        category_id=p.category_id,
        supplier_id=p.supplier_id,
        port_id=p.port_id,
    )
    return {
        "id": p.id,
        "revision": p.revision,
        "price_version": p.price_version,
        "product_name_en": p.product_name_en,
        "product_name_jp": p.product_name_jp,
        "code": p.code,
        "country_id": p.country_id,
        "category_id": p.category_id,
        "supplier_id": p.supplier_id,
        "port_id": p.port_id,
        "country_name": names["country_name"],
        "category_name": names["category_name"],
        "supplier_name": names["supplier_name"],
        "port_name": names["port_name"],
        "unit": p.unit,
        "price": float(p.price) if p.price is not None else None,
        "contract_price": (
            float(p.contract_price) if p.contract_price is not None else None
        ),
        "purchase_price_effective_from": (
            str(p.purchase_price_effective_from)
            if p.purchase_price_effective_from else None
        ),
        "purchase_price_effective_to": (
            str(p.purchase_price_effective_to)
            if p.purchase_price_effective_to else None
        ),
        "selling_price_effective_from": (
            str(p.selling_price_effective_from)
            if p.selling_price_effective_from else None
        ),
        "selling_price_effective_to": (
            str(p.selling_price_effective_to)
            if p.selling_price_effective_to else None
        ),
        "price_periods": price_period_projection,
        "unit_size": p.unit_size,
        "pack_size": p.pack_size,
        "country_of_origin": p.country_of_origin,
        "brand": p.brand,
        "currency": p.currency,
        "effective_from": str(p.effective_from) if p.effective_from else None,
        "effective_to": str(p.effective_to) if p.effective_to else None,
        "status": p.status,
        # Computed at read time; UI uses this for the status badge so
        # expired products (effective_to < today) flip to 无效 without
        # any cron / migration. `status` stays the source of truth for
        # the manual admin toggle.
        "is_effective": _is_effective(p),
        # R5 (2026-06-22): list-page projection for the image column.
        # `thumbnail_url` is a freshly-signed URL to the primary image
        # (display_order=0) or None when the product has no images.
        # `image_count` powers the "+N" overlay on the cell.
        "thumbnail_url": thumbnail_url,
        "image_count": image_count,
    }
