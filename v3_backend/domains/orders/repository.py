"""DB access for the orders domain."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from domains.orders.models import Order


def get(db: Session, order_id: int) -> Order | None:
    return db.get(Order, order_id)


def get_by_document_id(db: Session, document_id: int) -> Order | None:
    """Return the Order created from this Document, or None.

    Was `scalar_one_or_none()` originally — that assumed strictly 0..1
    rows. Prod hit `MultipleResultsFound` 2026-06-22 because a stale
    create flow plus the original P0 async refactor could leave the same
    document with multiple Order rows. The write path is now idempotent,
    but this lookup still tolerates historical duplicates and returns the
    most recently created Order for the document detail link.

    `find_order_ids_linked_to_document` is the right tool when you need
    the full set (delete path); this one is for "show the user one
    Order to open".
    """
    return (
        db.execute(
            select(Order)
            .where(Order.document_id == document_id)
            .order_by(Order.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def list_page(
    db: Session,
    *,
    user_id: int | None = None,
    include_all_users: bool = False,
    status: str | None = None,
    fulfillment_status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[int, list[Order]]:
    stmt = select(Order)
    if not include_all_users and user_id is not None:
        stmt = stmt.where(Order.user_id == user_id)
    if status:
        stmt = stmt.where(Order.status == status)
    if fulfillment_status:
        stmt = stmt.where(Order.fulfillment_status == fulfillment_status)

    total = len(db.execute(stmt.with_only_columns(Order.id).order_by(None)).all())
    items = list(
        db.execute(stmt.order_by(Order.created_at.desc()).limit(limit).offset(offset)).scalars()
    )
    return total, items


def save(db: Session, order: Order) -> Order:
    from datetime import datetime

    order.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(order)
    return order


def create(db: Session, order: Order) -> Order:
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def delete(db: Session, order: Order) -> None:
    db.delete(order)
    db.commit()
