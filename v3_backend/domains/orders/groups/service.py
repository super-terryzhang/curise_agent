"""Order-groups service (R7).

Stateless CRUD + assignment. All operations check ownership: an employee
only sees / mutates their own groups. Admins / superadmins use the same
endpoints — at HTTP layer they're allowed to override `user_id` via
admin tooling if needed (not in the current scope; matches the
"user-scoped by default" convention of the orders domain).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, or_
from sqlalchemy.orm import Session

from domains.identity import User
from domains.orders.groups.automation import lock_grouping, mark_manual
from domains.orders.groups.schemas import (
    OrderGroupAssignBody,
    OrderGroupCreate,
    OrderGroupUpdate,
)
from domains.orders.models import Order, OrderGroup


class OrderGroupError(Exception):
    """Base class for translation to HTTP 4xx in the router layer."""


class NotFound(OrderGroupError):
    pass


class BadRequest(OrderGroupError):
    pass


# ─── Helpers ──────────────────────────────────────────────────


def _serialize(group: OrderGroup, order_count: int, *, can_manage=True) -> dict[str, Any]:
    return {
        "id": group.id,
        "user_id": group.user_id,
        "name": group.name,
        "ship_name": group.ship_name,
        "loading_date": group.loading_date,
        "order_count": order_count,
        "can_manage": can_manage,
        "created_at": group.created_at,
        "updated_at": group.updated_at,
    }


def _load(db: Session, group_id: int, user_id: int) -> OrderGroup:
    """Load a group owned by `user_id` or raise NotFound.

    NotFound (not Forbidden) is intentional — surfacing existence to a
    non-owner is itself a leak. Matches the documents domain pattern.
    """
    group = db.get(OrderGroup, group_id)
    if group is None or not (_admin(db, user_id) or group.user_id == user_id or db.query(Order.id).filter_by(group_id=group_id, user_id=user_id).first()):
        raise NotFound("分组不存在")
    return group


def _count_orders(db: Session, group_id: int) -> int:
    return int(
        db.execute(
            select(func.count(Order.id)).where(Order.group_id == group_id)
        ).scalar()
        or 0
    )


# ─── Public API ───────────────────────────────────────────────


def _admin(db, user_id):
    user = db.get(User, user_id)
    return bool(user and user.role in ("admin", "superadmin"))


def _can_manage(db, group, user_id):
    return _admin(db, user_id) or (group.user_id == user_id and not db.query(Order.id).filter(
        Order.group_id == group.id, Order.user_id != user_id
    ).first())


def require_manage(db, group_id, user_id):
    group = _load(db, group_id, user_id)
    if not _can_manage(db, group, user_id):
        raise BadRequest("跨账号分组由管理员管理；你仍可查看或移除自己的订单")
    return group


def _view(db, group, user_id):
    query = db.query(Order).filter_by(group_id=group.id)
    if not _admin(db, user_id):
        query = query.filter_by(user_id=user_id)
    result = _serialize(group, query.count(), can_manage=_can_manage(db, group, user_id))
    result["can_generate_inquiry"] = result["can_manage"] and db.query(
        Order.user_id).filter_by(group_id=group.id).distinct().count() <= 1
    return result


def require_group_inquiry(db, group_id, user_id):
    group = require_manage(db, group_id, user_id)
    if not _view(db, group, user_id)["can_generate_inquiry"]:
        raise BadRequest("跨账号分组请按订单分别生成询价，避免合并文件暴露其他账号的订单")
    return group


def list_groups(db: Session, *, user_id: int) -> list[dict[str, Any]]:
    query = db.query(OrderGroup)
    if not _admin(db, user_id):
        query = query.filter(or_(OrderGroup.user_id == user_id, OrderGroup.id.in_(
            select(Order.group_id).where(Order.user_id == user_id))))
    groups = query.order_by(OrderGroup.created_at.desc(), OrderGroup.id.desc()).all()
    return [_view(db, group, user_id) for group in groups]


def get_group(db: Session, *, group_id: int, user_id: int) -> dict[str, Any]:
    g = _load(db, group_id, user_id)
    return _view(db, g, user_id)


def create_group(
    db: Session, *, user_id: int, body: OrderGroupCreate
) -> dict[str, Any]:
    lock_grouping(db)
    now = datetime.utcnow()
    g = OrderGroup(
        user_id=user_id,
        name=body.name,
        ship_name=body.ship_name,
        loading_date=body.loading_date,
        created_at=now,
        updated_at=now,
    )
    db.add(g)
    db.flush()  # need g.id for the assignment below

    initial_count = 0
    if body.order_ids:
        initial_count = _assign_orders(
            db, group_id=g.id, user_id=user_id, order_ids=body.order_ids
        )

    db.commit()
    db.refresh(g)
    return _serialize(g, initial_count)


def update_group(
    db: Session, *, group_id: int, user_id: int, body: OrderGroupUpdate
) -> dict[str, Any]:
    lock_grouping(db)
    g = require_manage(db, group_id, user_id)
    for order in db.query(Order).filter_by(group_id=g.id).all():
        mark_manual(order)
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(g, k, v)
    g.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(g)
    return _view(db, g, user_id)


def delete_group(db: Session, *, group_id: int, user_id: int) -> None:
    lock_grouping(db)
    g = require_manage(db, group_id, user_id)
    member_ids = [o.id for o in db.query(Order).filter_by(group_id=g.id).all()]
    # ON DELETE SET NULL on Order.group_id (migration 0015) handles the
    # cascade. We still null it explicitly here for the test DB where
    # SQLite doesn't enforce FK cascades by default.
    db.execute(
        Order.__table__.update()
        .where(Order.group_id == g.id)
        .values(group_id=None)
    )
    for order in db.query(Order).filter(Order.id.in_(member_ids)).all():
        mark_manual(order)
    db.delete(g)
    db.commit()


def assign_orders(
    db: Session, *, group_id: int, user_id: int, body: OrderGroupAssignBody
) -> dict[str, Any]:
    lock_grouping(db)
    g = require_manage(db, group_id, user_id)
    _assign_orders(
        db, group_id=g.id, user_id=user_id, order_ids=body.order_ids
    )
    db.commit()
    db.refresh(g)
    return _view(db, g, user_id)


def remove_order(
    db: Session, *, group_id: int, user_id: int, order_id: int
) -> None:
    lock_grouping(db)
    g = _load(db, group_id, user_id)
    order = db.get(Order, order_id)
    if order is None or (order.user_id != user_id and not _admin(db, user_id)):
        # Same pattern as _load — leakage-safe NotFound.
        raise NotFound("订单不存在")
    if order.group_id != g.id:
        # Already not in this group — idempotent no-op.
        return
    order.group_id = None
    mark_manual(order)
    db.commit()


def _assign_orders(
    db: Session, *, group_id: int, user_id: int, order_ids: list[int]
) -> int:
    """Set Order.group_id for orders owned by user. Returns updated count.

    Silently ignores order_ids the user doesn't own (or that don't
    exist). The list endpoint will reflect the actual count, so the
    user sees what landed. This matches the spirit of "merge what you
    selected" — no per-id error popups for race conditions.
    """
    if not order_ids:
        return 0
    query = db.query(Order).filter(Order.id.in_(set(order_ids)))
    if not _admin(db, user_id):
        query = query.filter(Order.user_id == user_id)
    orders = query.all()
    for order in orders:
        order.group_id = group_id
        mark_manual(order)
    # An explicit manual assignment takes control of this group's membership.
    for order in db.query(Order).filter_by(group_id=group_id).all():
        mark_manual(order)
    return len(orders)
