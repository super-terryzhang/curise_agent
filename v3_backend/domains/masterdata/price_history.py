"""Product-scoped price events. Writers share their product transaction."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from domains.masterdata.errors import BadRequest, Conflict, NotFound
from domains.masterdata.models import Product, ProductPriceHistory

PRICE_FIELDS = (
    "price",
    "contract_price",
    "purchase_price_effective_from",
    "purchase_price_effective_to",
    "selling_price_effective_from",
    "selling_price_effective_to",
    "currency",
    "unit",
    "unit_size",
    "pack_size",
    "supplier_id",
    "country_id",
    "port_id",
)
SNAPSHOT_FIELDS = ("id", "product_name_en", "code", *PRICE_FIELDS)


def snapshot(product: Product) -> dict:
    result = {key: getattr(product, key) for key in SNAPSHOT_FIELDS}
    for field in ("price", "contract_price"):
        value = result[field]
        result[field] = format(Decimal(str(value)), ".2f") if value is not None else None
    for field in (
        "purchase_price_effective_from",
        "purchase_price_effective_to",
        "selling_price_effective_from",
        "selling_price_effective_to",
    ):
        value = result[field]
        result[field] = value.date().isoformat() if value is not None else None
    return result


def lock_product(db: Session, product_id: int, expected_revision: int | None = None) -> Product:
    product = db.scalar(
        select(Product)
        .where(Product.id == product_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if product is None:
        raise NotFound("产品不存在")
    if expected_revision is not None and product.revision != expected_revision:
        raise Conflict(
            f"产品 #{product_id} 已被修改，请刷新后重新确认（当前版本 {product.revision}）"
        )
    return product


def _event(
    db: Session,
    product: Product,
    before: dict | None,
    after: dict | None,
    *,
    event_type: str,
    actor_id: int | None,
    source: str,
    batch_id: int | None = None,
    restores_id: str | None = None,
    source_key: str | None = None,
) -> ProductPriceHistory:
    fields = [k for k in PRICE_FIELDS if (before or {}).get(k) != (after or {}).get(k)]
    if event_type in ("initial", "baseline"):
        fields = []
    event = ProductPriceHistory(
        id=uuid4().hex,
        product_id=product.id,
        version=product.price_version,
        event_type=event_type,
        before_price=(before or {}).get("price"),
        after_price=(after or {}).get("price"),
        before_contract_price=(before or {}).get("contract_price"),
        after_contract_price=(after or {}).get("contract_price"),
        before_context=before,
        after_context=after,
        changed_fields=fields,
        actor_id=actor_id,
        source=source,
        source_batch_id=batch_id,
        source_key=source_key,
        restores_id=restores_id,
        recorded_at=datetime.now(UTC),
    )
    db.add(event)
    return event


def initial(
    db: Session, product: Product, *, actor_id: int | None, source: str, batch_id: int | None = None
) -> ProductPriceHistory:
    db.flush()  # product identity and its initial revision are now allocated
    return _event(
        db,
        product,
        None,
        snapshot(product),
        event_type="initial",
        actor_id=actor_id,
        source=source,
        batch_id=batch_id,
    )


def ensure_baseline(db: Session, product: Product, before: dict) -> None:
    existing = db.scalar(
        select(ProductPriceHistory.id).where(
            ProductPriceHistory.product_id == product.id, ProductPriceHistory.version == 0
        )
    )
    if existing is None:
        # Compatibility for controlled imports/test fixtures; deployment migration
        # creates all normal baselines before enabling any new writer.
        if product.price_version != 0:
            raise Conflict("价格账本基线缺失，请检查迁移状态")
        _event(
            db,
            product,
            None,
            before,
            event_type="baseline",
            actor_id=None,
            source="migration",
            source_key=f"baseline:{product.id}",
        )


def record_change(
    db: Session,
    product: Product,
    before: dict,
    *,
    actor_id: int | None,
    source: str,
    batch_id: int | None = None,
    event_type: str = "change",
    restores_id: str | None = None,
) -> ProductPriceHistory | None:
    after = None if event_type == "delete" else snapshot(product)
    if after is not None and all(before.get(k) == after.get(k) for k in PRICE_FIELDS):
        return None
    ensure_baseline(db, product, before)
    product.price_version += 1
    return _event(
        db,
        product,
        before,
        after,
        event_type=event_type,
        actor_id=actor_id,
        source=source,
        batch_id=batch_id,
        restores_id=restores_id,
    )


def serialize(event: ProductPriceHistory) -> dict:
    timestamp = event.recorded_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return {
        "id": event.id,
        "product_id": event.product_id,
        "version": event.version,
        "event_type": event.event_type,
        "recorded_at": timestamp.isoformat(),
        "before": event.before_context,
        "after": event.after_context,
        "changed_fields": event.changed_fields,
        "actor_id": event.actor_id,
        "source": event.source,
        "source_batch_id": event.source_batch_id,
        "restores_id": event.restores_id,
    }


def list_history(
    db: Session,
    product_id: int,
    *,
    limit: int = 20,
    offset: int = 0,
    max_version: int | None = None,
    field: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    legacy: bool = False,
) -> dict:
    if field not in (None, "price", "contract_price"):
        raise BadRequest("只支持采购价或卖价筛选")
    # Treat timezone-free API bounds as UTC, matching ledger storage.
    if date_from and date_from.tzinfo is None:
        date_from = date_from.replace(tzinfo=UTC)
    if date_to and date_to.tzinfo is None:
        date_to = date_to.replace(tzinfo=UTC)
    if date_from and date_to and date_from > date_to:
        raise BadRequest("开始日期不能晚于结束日期")
    product = db.get(Product, product_id)
    base = select(ProductPriceHistory).where(
        ProductPriceHistory.product_id == product_id,
        ProductPriceHistory.version.is_(None)
        if legacy
        else ProductPriceHistory.version.is_not(None),
    )
    latest = db.scalar(
        select(func.max(ProductPriceHistory.version)).where(
            ProductPriceHistory.product_id == product_id
        )
    )
    any_history = db.scalar(
        select(ProductPriceHistory.id).where(ProductPriceHistory.product_id == product_id).limit(1)
    )
    if product is None and any_history is None:
        raise NotFound("产品及价格历史不存在")
    ceiling = latest if max_version is None else min(max_version, latest or 0)
    if not legacy:
        base = base.where(ProductPriceHistory.version <= (ceiling or 0))
    if field:
        old = getattr(ProductPriceHistory, "before_" + field)
        new = getattr(ProductPriceHistory, "after_" + field)
        base = base.where(old.is_distinct_from(new))
    if date_from:
        base = base.where(ProductPriceHistory.recorded_at >= date_from)
    if date_to:
        base = base.where(ProductPriceHistory.recorded_at <= date_to)
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    changes = db.scalar(
        select(func.count()).select_from(
            base.where(ProductPriceHistory.event_type.in_(("change", "restore"))).subquery()
        )
    )
    events = db.scalars(
        base.order_by(
            ProductPriceHistory.version.desc(),
            ProductPriceHistory.source_key,
            ProductPriceHistory.id,
        )
        .limit(limit)
        .offset(offset)
    ).all()
    legacy_count = db.scalar(
        select(func.count())
        .select_from(ProductPriceHistory)
        .where(
            ProductPriceHistory.product_id == product_id, ProductPriceHistory.event_type == "legacy"
        )
    )
    return {
        "product_id": product_id,
        "current": snapshot(product) if product else None,
        "revision": product.revision if product else None,
        "deleted": product is None,
        "total": total,
        "change_count": changes,
        "max_version": ceiling,
        "items": [serialize(e) for e in events],
        "legacy_count": legacy_count,
        "has_more": offset + len(events) < total,
    }


def restore(
    db: Session,
    product_id: int,
    event_id: str,
    expected_revision: int,
    *,
    actor_id: int,
    source: str = "http",
) -> dict:
    event = db.get(ProductPriceHistory, event_id)
    if event is None or event.product_id != product_id:
        raise NotFound("此产品的历史记录不存在")
    if event.event_type in ("legacy", "delete") or event.after_context is None:
        raise BadRequest("此记录没有完整价格快照，不能直接恢复")
    from domains.masterdata._products_service import update_product
    from domains.masterdata.schemas import ProductUpdate

    body = ProductUpdate(
        **{
            key: event.after_context.get(key)
            for key in PRICE_FIELDS
            if key in event.after_context
        },
        expected_revision=expected_revision,
    )
    return update_product(
        db,
        product_id,
        body,
        actor_id=actor_id,
        source=source,
        event_type="restore",
        restores_id=event.id,
    )
