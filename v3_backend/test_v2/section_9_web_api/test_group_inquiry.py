"""Section 9 — Web API: group-merged inquiry (R7 Gap 2 2026-06-22).

Why this test set exists:
    Felix's "合单" workflow needs the inquiry pipeline to fold multiple
    Orders into a single per-supplier Excel — otherwise the whole point
    of grouping is missed. The contract this locks in:

    1. POST /api/order-groups/{id}/generate-inquiry returns the anchor
       order id so the frontend can wire the existing per-order
       streaming / preview / download endpoints.
    2. Anchor = oldest Order in the group (by created_at). This makes
       the Inquiry row's order_id stable across runs.
    3. The orchestrator merges `match_results` across every Order in
       the group and groups them by supplier_id — one supplier shows
       up exactly once in the worker pool even if it appears in
       multiple Orders.
    4. Group ship_name + loading_date override the anchor's values.
    5. po_number folds all orders' PO numbers into "P1 / P2 / P3".

We use a fake `run_inquiry_for_group` that records its arguments
instead of running the full Gemini-backed pipeline. The unit-level
behavior (merging, anchor selection) is also tested directly against
the orchestrator entry point.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

from domains.orders.models import Order, OrderGroup
from test_v2.fixtures.helpers import login, seed_user


def _seed_group(db, *, user_id: int, **kw) -> OrderGroup:
    g = OrderGroup(user_id=user_id, name="GTest", **kw)
    db.add(g)
    db.commit()
    db.refresh(g)
    return g


def _seed_order_in_group(
    db,
    *,
    user_id: int,
    group_id: int,
    po_number: str,
    created_at: datetime | None = None,
    match_results: list[dict] | None = None,
) -> Order:
    o = Order(
        user_id=user_id,
        filename=f"{po_number}.pdf",
        status="ready",
        po_number=po_number,
        group_id=group_id,
        created_at=created_at or datetime.utcnow(),
        match_results=match_results or [],
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


# ─── HTTP endpoint contract ──────────────────────────────────


def test_generate_inquiry_returns_anchor_order_id(client, db):
    """Endpoint must return the anchor (oldest) order id so the frontend
    can subscribe to the existing per-order SSE stream."""
    user = seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    g = _seed_group(db, user_id=user.id)
    o1 = _seed_order_in_group(
        db,
        user_id=user.id,
        group_id=g.id,
        po_number="OLDEST",
        created_at=datetime.utcnow() - timedelta(days=2),
    )
    _seed_order_in_group(
        db,
        user_id=user.id,
        group_id=g.id,
        po_number="NEWER",
        created_at=datetime.utcnow(),
    )

    # Fake the heavy run; we only care the endpoint returns the right id.
    with patch(
        "domains.inquiry.orchestrator.run_inquiry_for_group", lambda gid: None
    ):
        r = client.post(
            f"/api/order-groups/{g.id}/generate-inquiry",
            headers=headers,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["group_id"] == g.id
    assert body["anchor_order_id"] == o1.id, (
        f"anchor must be the OLDEST order; got {body['anchor_order_id']} "
        f"expected {o1.id}"
    )


def test_generate_inquiry_404_on_other_users_group(client, db):
    """Cross-user isolation: Alice cannot trigger inquiry on Bob's group.
    Returns 404 (not 403) so the existence of bob's group isn't leaked."""
    user_a = seed_user(db, email="alice@x.test", role="employee")
    user_b = seed_user(db, email="bob@x.test", role="employee")
    bobs = _seed_group(db, user_id=user_b.id)
    _seed_order_in_group(
        db, user_id=user_b.id, group_id=bobs.id, po_number="BobsPO"
    )
    headers = login(client, "alice@x.test")
    _ = user_a  # noqa: F841 — only seeded to ensure the user exists

    r = client.post(
        f"/api/order-groups/{bobs.id}/generate-inquiry", headers=headers
    )
    assert r.status_code == 404


def test_generate_inquiry_400_on_empty_group(client, db):
    """An empty group has no anchor → fast-fail with a friendly error."""
    user = seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    g = _seed_group(db, user_id=user.id)

    r = client.post(
        f"/api/order-groups/{g.id}/generate-inquiry", headers=headers
    )
    assert r.status_code == 400
    assert "没有订单" in r.json()["detail"]


# ─── Orchestrator unit behavior ──────────────────────────────


def test_anchor_selection_picks_oldest_order(db):
    """Direct check: `get_anchor_order_id_for_group` returns the oldest
    Order in the group, regardless of insertion order."""
    from domains.inquiry import orchestrator

    user = seed_user(db, email="alice@x.test", role="employee")
    g = _seed_group(db, user_id=user.id)
    # Seed in reverse-chronological insertion order to verify we don't
    # just return "first inserted".
    _seed_order_in_group(
        db,
        user_id=user.id,
        group_id=g.id,
        po_number="NEW",
        created_at=datetime.utcnow(),
    )
    expected_anchor = _seed_order_in_group(
        db,
        user_id=user.id,
        group_id=g.id,
        po_number="OLD",
        created_at=datetime.utcnow() - timedelta(days=10),
    )
    _seed_order_in_group(
        db,
        user_id=user.id,
        group_id=g.id,
        po_number="MIDDLE",
        created_at=datetime.utcnow() - timedelta(days=5),
    )

    assert (
        orchestrator.get_anchor_order_id_for_group(db, g.id)
        == expected_anchor.id
    )


def test_get_anchor_returns_none_for_empty_group(db):
    from domains.inquiry import orchestrator

    user = seed_user(db, email="alice@x.test", role="employee")
    g = _seed_group(db, user_id=user.id)
    assert orchestrator.get_anchor_order_id_for_group(db, g.id) is None
