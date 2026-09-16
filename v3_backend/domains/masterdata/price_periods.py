"""Canonical multi-period purchase/selling prices for products."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domains.masterdata.errors import BadRequest, Conflict, NotFound
from domains.masterdata.models import Product, ProductPricePeriod
from domains.masterdata.schemas import ProductPricePeriodCreate, ProductPricePeriodUpdate

PRICE_TYPES = {"purchase", "selling"}
_LABELS = {"purchase": "采购价", "selling": "卖价"}


def list_periods(db: Session, product_id: int) -> list[dict[str, Any]]:
    _require_product(db, product_id)
    rows = db.scalars(
        select(ProductPricePeriod)
        .where(ProductPricePeriod.product_id == product_id)
        .order_by(
            ProductPricePeriod.price_type,
            ProductPricePeriod.effective_from.desc(),
            ProductPricePeriod.id.desc(),
        )
    ).all()
    return [serialize(row) for row in rows]


def periods_by_product(
    db: Session, product_ids: list[int]
) -> dict[int, list[dict[str, Any]]]:
    if not product_ids:
        return {}
    rows = db.scalars(
        select(ProductPricePeriod)
        .where(ProductPricePeriod.product_id.in_(product_ids))
        .order_by(
            ProductPricePeriod.product_id,
            ProductPricePeriod.price_type,
            ProductPricePeriod.effective_from.desc(),
            ProductPricePeriod.id.desc(),
        )
    ).all()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.product_id, []).append(serialize(row))
    return grouped


def create_period(
    db: Session,
    product_id: int,
    body: ProductPricePeriodCreate,
    *,
    actor_id: int | None,
    source: str = "http",
    source_batch_id: int | None = None,
) -> dict[str, Any]:
    product = _lock_product(db, product_id)
    price_type = _validate_type(body.price_type)
    _validate_dates(body.effective_from, body.effective_to, price_type)
    _assert_no_overlap(
        db,
        product_id=product_id,
        price_type=price_type,
        effective_from=body.effective_from,
        effective_to=body.effective_to,
    )
    row = ProductPricePeriod(
        product_id=product_id,
        price_type=price_type,
        amount=Decimal(str(body.amount)),
        currency=body.currency or product.currency,
        effective_from=body.effective_from,
        effective_to=body.effective_to,
        status=True,
        source=source,
        source_batch_id=source_batch_id,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(row)
    product.price_version += 1
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("相同价格期间已经存在，请刷新后检查") from exc
    db.refresh(row)
    return serialize(row)


def update_period(
    db: Session,
    product_id: int,
    period_id: int,
    body: ProductPricePeriodUpdate,
    *,
    actor_id: int | None,
) -> dict[str, Any]:
    product = _lock_product(db, product_id)
    row = db.get(ProductPricePeriod, period_id)
    if row is None or row.product_id != product_id:
        raise NotFound("价格期间不存在")
    data = body.model_dump(exclude_unset=True)
    effective_from = data.get("effective_from", row.effective_from)
    effective_to = data.get("effective_to", row.effective_to)
    _validate_dates(effective_from, effective_to, row.price_type)
    active = data.get("status", row.status)
    if active:
        _assert_no_overlap(
            db,
            product_id=product_id,
            price_type=row.price_type,
            effective_from=effective_from,
            effective_to=effective_to,
            exclude_id=row.id,
        )
    for field, value in data.items():
        if field == "amount" and value is not None:
            value = Decimal(str(value))
        setattr(row, field, value)
    row.updated_by = actor_id
    row.updated_at = datetime.utcnow()
    product.price_version += 1
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("相同价格期间已经存在，请刷新后检查") from exc
    db.refresh(row)
    return serialize(row)


def deactivate_period(
    db: Session, product_id: int, period_id: int, *, actor_id: int | None
) -> None:
    product = _lock_product(db, product_id)
    row = db.get(ProductPricePeriod, period_id)
    if row is None or row.product_id != product_id:
        raise NotFound("价格期间不存在")
    row.status = False
    row.updated_by = actor_id
    row.updated_at = datetime.utcnow()
    product.price_version += 1
    db.commit()


def attach_effective_prices(
    db: Session, products: list[Product], effective_on: date | None
) -> None:
    """Attach one resolved purchase/selling projection to each loaded product."""
    if not products:
        return
    ids = [product.id for product in products]
    periods = db.scalars(
        select(ProductPricePeriod).where(
            ProductPricePeriod.product_id.in_(ids),
            ProductPricePeriod.status.is_(True),
        )
    ).all()
    by_product: dict[int, dict[str, list[ProductPricePeriod]]] = {}
    for period in periods:
        by_product.setdefault(period.product_id, {}).setdefault(
            period.price_type, []
        ).append(period)

    for product in products:
        configured = by_product.get(product.id, {})
        product._effective_prices = {  # type: ignore[attr-defined]
            price_type: _resolve_one(
                product,
                price_type,
                configured.get(price_type, []),
                effective_on,
            )
            for price_type in PRICE_TYPES
        }


def sync_compatibility_periods(
    db: Session,
    product: Product,
    *,
    actor_id: int | None,
    source: str,
    source_batch_id: int | None = None,
) -> None:
    """Project the legacy one-period fields into canonical child records.

    Incomplete legacy dates remain on Product and are intentionally not turned
    into an open-ended interval; matching will continue to use the legacy price
    with a warning until users supply both boundaries.
    """
    specs = (
        (
            "purchase",
            product.price,
            product.purchase_price_effective_from,
            product.purchase_price_effective_to,
        ),
        (
            "selling",
            product.contract_price,
            product.selling_price_effective_from,
            product.selling_price_effective_to,
        ),
    )
    for price_type, amount, raw_start, raw_end in specs:
        if amount is None or raw_start is None or raw_end is None:
            continue
        effective_from = raw_start.date() if isinstance(raw_start, datetime) else raw_start
        effective_to = raw_end.date() if isinstance(raw_end, datetime) else raw_end
        _validate_dates(effective_from, effective_to, price_type)
        existing = db.scalar(
            select(ProductPricePeriod).where(
                ProductPricePeriod.product_id == product.id,
                ProductPricePeriod.price_type == price_type,
                ProductPricePeriod.effective_from == effective_from,
                ProductPricePeriod.effective_to == effective_to,
            )
        )
        if existing is not None:
            existing.amount = Decimal(str(amount))
            existing.currency = product.currency
            existing.status = True
            existing.updated_by = actor_id
            existing.updated_at = datetime.utcnow()
            continue
        _assert_no_overlap(
            db,
            product_id=product.id,
            price_type=price_type,
            effective_from=effective_from,
            effective_to=effective_to,
        )
        db.add(
            ProductPricePeriod(
                product_id=product.id,
                price_type=price_type,
                amount=Decimal(str(amount)),
                currency=product.currency,
                effective_from=effective_from,
                effective_to=effective_to,
                source=source,
                source_batch_id=source_batch_id,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )


def serialize(row: ProductPricePeriod) -> dict[str, Any]:
    return {
        "id": row.id,
        "product_id": row.product_id,
        "price_type": row.price_type,
        "amount": float(row.amount),
        "currency": row.currency,
        "effective_from": row.effective_from.isoformat(),
        "effective_to": row.effective_to.isoformat(),
        "status": row.status,
        "source": row.source,
        "source_batch_id": row.source_batch_id,
        "created_by": row.created_by,
        "updated_by": row.updated_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _resolve_one(
    product: Product,
    price_type: str,
    periods: list[ProductPricePeriod],
    effective_on: date | None,
) -> dict[str, Any]:
    label = _LABELS[price_type]
    fallback = product.price if price_type == "purchase" else product.contract_price
    if not periods:
        return {
            "amount": float(fallback) if fallback is not None else None,
            "currency": product.currency,
            "period_id": None,
            "effective_from": None,
            "effective_to": None,
            "source": "legacy",
            "warning": f"{label}期间未配置",
        }
    if effective_on is None:
        return {
            "amount": None,
            "currency": product.currency,
            "period_id": None,
            "effective_from": None,
            "effective_to": None,
            "source": "period",
            "warning": f"缺少装船日，无法选择{label}期间",
        }
    matches = [
        row
        for row in periods
        if row.effective_from <= effective_on <= row.effective_to
    ]
    if len(matches) > 1:
        raise Conflict(f"产品 #{product.id} 的{label}期间重叠，请先修复数据")
    if not matches:
        return {
            "amount": None,
            "currency": product.currency,
            "period_id": None,
            "effective_from": None,
            "effective_to": None,
            "source": "period",
            "warning": f"装船日 {effective_on.isoformat()} 未命中{label}期间",
        }
    row = matches[0]
    return {
        "amount": float(row.amount),
        "currency": row.currency or product.currency,
        "period_id": row.id,
        "effective_from": row.effective_from.isoformat(),
        "effective_to": row.effective_to.isoformat(),
        "source": "period",
        "warning": None,
    }


def _require_product(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise NotFound("产品不存在")
    return product


def _lock_product(db: Session, product_id: int) -> Product:
    product = db.scalar(
        select(Product).where(Product.id == product_id).with_for_update()
    )
    if product is None:
        raise NotFound("产品不存在")
    return product


def _validate_type(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in PRICE_TYPES:
        raise BadRequest("价格类型只能是 purchase（采购价）或 selling（卖价）")
    return normalized


def _validate_dates(effective_from: date, effective_to: date, price_type: str) -> None:
    if effective_from > effective_to:
        raise BadRequest(f"{_LABELS[price_type]}有效开始日期不能晚于结束日期")


def _assert_no_overlap(
    db: Session,
    *,
    product_id: int,
    price_type: str,
    effective_from: date,
    effective_to: date,
    exclude_id: int | None = None,
) -> None:
    stmt = select(ProductPricePeriod.id).where(
        ProductPricePeriod.product_id == product_id,
        ProductPricePeriod.price_type == price_type,
        ProductPricePeriod.status.is_(True),
        ProductPricePeriod.effective_from <= effective_to,
        ProductPricePeriod.effective_to >= effective_from,
    )
    if exclude_id is not None:
        stmt = stmt.where(ProductPricePeriod.id != exclude_id)
    if db.scalar(stmt.limit(1)) is not None:
        raise Conflict(f"{_LABELS[price_type]}有效期间与已有记录重叠")
