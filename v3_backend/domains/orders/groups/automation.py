"""Automatic display grouping. Keeps independent orders and inquiry files."""
import logging
from collections import defaultdict
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import load_only

from domains.masterdata import Port
from domains.orders.groups.rules import grouping_identity, identity_key
from domains.orders.models import Order, OrderGroup

MARKER = "_automatic_grouping"
logger = logging.getLogger(__name__)


def lock_grouping(db):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(726091, 1)"))


def mark_manual(order):
    order.order_metadata = {**(order.order_metadata or {}), MARKER: {"mode": "manual"}}


def regroup(db, *, apply=False, focus_order_id=None):
    # One short transaction for group membership across API and import workers.
    # PostgreSQL advisory locks are transaction-scoped, released on rollback too.
    if apply:
        lock_grouping(db)
    orders = db.query(Order).options(load_only(
        Order.id, Order.user_id, Order.group_id, Order.order_metadata,
        Order.ship_name, Order.loading_date, Order.delivery_date,
        Order.destination_port, Order.port_id,
    )).order_by(Order.id).populate_existing().all()
    ports = {p.id: p for p in db.query(Port).all()}
    by_group = defaultdict(list)
    for order in orders:
        if order.group_id:
            by_group[order.group_id].append(order)
    managed = {gid for gid, members in by_group.items() if all(
        (o.order_metadata or {}).get(MARKER, {}).get("group_id") == gid
        and (o.order_metadata or {}).get(MARKER, {}).get("mode") == "auto"
        for o in members
    )}
    buckets = defaultdict(list)
    identities = {}
    skipped = []
    for order in orders:
        marker = (order.order_metadata or {}).get(MARKER, {})
        if marker.get("mode") == "manual" or (order.group_id and order.group_id not in managed):
            continue
        identity, reason = grouping_identity(order, ports)
        if reason:
            skipped.append({"order_id": order.id, "reason": reason})
            continue
        key = identity_key(identity)
        identities[order.id] = identity
        buckets[key].append(order)
    # On new/edit, only touch that order and groups affected by its old/new key.
    focus = next((o for o in orders if o.id == focus_order_id), None)
    keys = set(buckets)
    if focus_order_id is not None:
        keys = {k for k, members in buckets.items() if any(o.id == focus_order_id for o in members)}
        if focus and focus.group_id in managed:
            keys.update(k for k, members in buckets.items() if any(o.group_id == focus.group_id for o in members))
    plans = []
    assigned = 0
    created = 0
    reused = set()
    for key in sorted(keys):
        members = buckets[key]
        # Reuse only a group whose ORIGINAL recorded identity matches this key.
        candidates = sorted({o.group_id for o in members if o.group_id in managed and
            tuple((o.order_metadata or {}).get(MARKER, {}).get("key", [])) == key})
        gid = next((g for g in candidates if g not in reused), None)
        # A supply arrangement exists even when only one PO is known yet.
        identity = identities[members[0].id]
        plans.append({**identity, "group_id": gid, "order_ids": [o.id for o in members]})
        if not apply:
            continue
        group = db.get(OrderGroup, gid) if gid else None
        if group is None:
            group = OrderGroup(user_id=members[0].user_id,
                name=f"{identity['day']} · {identity['port_label']}"[:200],
                ship_name=identity["ship"] or None,
                loading_date=identity["day"])
            db.add(group)
            db.flush()
            created += 1
        reused.add(group.id)
        plans[-1]["group_id"] = group.id
        for order in members:
            if order.group_id != group.id:
                order.group_id = group.id
                assigned += 1
            order.order_metadata = {**(order.order_metadata or {}), MARKER: {
                "mode": "auto", "group_id": group.id, "key": list(key), **identity}}
        group.updated_at = datetime.utcnow()
    # If edited dates/ports no longer match anything, remove only that automatic
    # membership. A manual group or manual removal is never reversed here.
    covered = {oid for plan in plans for oid in plan["order_ids"]}
    for order in orders:
        if order.group_id in managed and order.id not in covered and (
            focus_order_id is None or order.id == focus_order_id
        ) and apply:
            order.group_id = None
            order.order_metadata = {**(order.order_metadata or {}), MARKER: {"mode": "auto"}}
    if apply:
        db.commit()
    return {"groups": plans, "created_groups": created, "assigned_orders": assigned,
            "skipped": skipped}


def auto_group_order(db, order_id):
    """Grouping failure must not turn a successful document import into failure."""
    try:
        return regroup(db, apply=True, focus_order_id=order_id)
    except Exception:
        db.rollback()
        logger.exception("automatic grouping failed for order %s", order_id)
        return {
            "groups": [],
            "created_groups": 0,
            "assigned_orders": 0,
            "skipped": [
                {
                    "order_id": order_id,
                    "reason": "自动归组失败，订单已进入未分类，请人工选择供船安排",
                }
            ],
            "reason": "自动归组失败，订单已进入未分类，请人工选择供船安排",
        }
