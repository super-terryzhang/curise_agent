"""Lock-in: rematch respects user-set port_id / country_id.

History:
    2026-05-29 — These tests were originally written as bug reproductions
    (failing on `main`). After the `_match_port` / `_match_country`
    priority-0 fix in `geo.py`, they now lock the post-fix behaviour.
    They are the regression net: if anyone touches geo.py and breaks
    "user override survives rematch", these go red first.

Scope:
    - rematch with no explicit override args → user's pre-set port_id
      survives (the UI's default flow — dialog sends {} per
      orders-api.ts:rematchOrder)
    - rematch with explicit port_id arg → caller intent survives
    - idempotent rematch (same data twice) → no flap
    - clearing order.port_id → matcher correctly re-derives from
      destination_port string (the escape hatch)
    - defensive: order.port_id refers to a deleted port → fall through
      to string priorities (not None / not crash)
    - country: same shape as port

If you change priority ordering in geo.py, expect at least 3 of these
to fail. Don't loosen the assertions — they encode the actual UX
contract the user reported on 2026-05-29.
"""

from __future__ import annotations

import pytest

from domains.masterdata.models import Country, Port
from domains.orders.models import Order
from domains.orders.service import rematch_order
from test_v2.fixtures.helpers import seed_user as _seed_user


@pytest.fixture
def user(db):
    return _seed_user(db, email="rematch-lock@example.com", role="superadmin")


@pytest.fixture
def geo_fixture(db):
    """Seed 2 countries + 4 ports (2 per country) so we can test
    cross-country port overrides as well as within-country swaps."""
    # `code` is required for the currency → country fallback path
    # (test_clearing_country_id_lets_currency_re_derive). Pre-fix
    # tests left it None and that test would never have exercised
    # the matcher's currency lookup.
    japan = Country(name="JAPAN", code="JPN")
    australia = Country(name="AUSTRALIA", code="AUS")
    db.add_all([japan, australia])
    db.flush()
    sydney = Port(name="SYDNEY", country_id=australia.id)
    auckland_jp = Port(name="AUCKLAND_JP", country_id=japan.id)
    tokyo = Port(name="TOKYO", country_id=japan.id)
    perth = Port(name="PERTH", country_id=australia.id)
    db.add_all([sydney, auckland_jp, tokyo, perth])
    db.commit()
    return {
        "japan": japan,
        "australia": australia,
        "sydney": sydney,
        "auckland_jp": auckland_jp,
        "tokyo": tokyo,
        "perth": perth,
    }


def _make_order(
    db,
    *,
    user_id: int,
    destination_port: str | None = None,
    port_id: int | None = None,
    country_id: int | None = None,
) -> Order:
    o = Order(
        user_id=user_id,
        filename="probe.pdf",
        file_type="pdf",
        status="ready_for_review",
        destination_port=destination_port,
        port_id=port_id,
        country_id=country_id,
        products=[],
        match_results=[],
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


# ─── Core contract: user override survives rematch ─────────────


def test_rematch_preserves_user_port_override_with_no_args(
    db, user, geo_fixture
):
    """UI default: 'rematch' dialog sends `{}`. Order has stale
    destination_port string but user-corrected port_id. Rematch must
    NOT re-derive port from the string."""
    sydney = geo_fixture["sydney"]
    tokyo = geo_fixture["tokyo"]

    order = _make_order(
        db,
        user_id=user.id,
        destination_port="SYDNEY",  # original PDF text
        port_id=tokyo.id,           # user corrected
    )

    rematch_order(db, order_id=order.id, user_id=user.id, is_admin=True)
    db.refresh(order)

    assert order.port_id == tokyo.id, (
        f"User changed port to Tokyo (#{tokyo.id}) but rematch reverted "
        f"to #{order.port_id} (likely Sydney from PDF string). "
        f"geo._match_port priority-0 broken."
    )


def test_rematch_preserves_explicit_port_arg(db, user, geo_fixture):
    """Caller passes port_id explicitly — must survive the geo step."""
    sydney = geo_fixture["sydney"]
    tokyo = geo_fixture["tokyo"]

    order = _make_order(
        db,
        user_id=user.id,
        destination_port="SYDNEY",
        port_id=sydney.id,
    )

    rematch_order(
        db,
        order_id=order.id,
        user_id=user.id,
        is_admin=True,
        port_id=tokyo.id,
    )
    db.refresh(order)

    assert order.port_id == tokyo.id


# ─── Idempotence ───────────────────────────────────────────────


def test_rematch_twice_with_no_changes_is_stable(db, user, geo_fixture):
    """Two consecutive rematches on the same order must converge to
    the same port. Catches a class of bugs where the matcher's output
    feeds back into next-rematch differently."""
    tokyo = geo_fixture["tokyo"]

    order = _make_order(
        db,
        user_id=user.id,
        destination_port="TOKYO",
        port_id=tokyo.id,
    )

    rematch_order(db, order_id=order.id, user_id=user.id, is_admin=True)
    db.refresh(order)
    first = order.port_id

    rematch_order(db, order_id=order.id, user_id=user.id, is_admin=True)
    db.refresh(order)
    second = order.port_id

    assert first == second == tokyo.id, (
        f"Idempotence broken: first rematch → #{first}, "
        f"second rematch → #{second}. Should both be Tokyo (#{tokyo.id})."
    )


# ─── Escape hatch ──────────────────────────────────────────────


def test_clearing_port_id_lets_rematch_re_derive_from_string(
    db, user, geo_fixture
):
    """Operator's escape hatch when they actually want the matcher to
    re-guess from the PDF text: set port_id=None, then rematch. The
    priority-0 check sees None and falls through to the string path.
    Without this hatch, users could not 'reset' a wrongly-set port."""
    sydney = geo_fixture["sydney"]

    order = _make_order(
        db,
        user_id=user.id,
        destination_port="SYDNEY",
        port_id=None,  # explicitly cleared
    )

    rematch_order(db, order_id=order.id, user_id=user.id, is_admin=True)
    db.refresh(order)

    assert order.port_id == sydney.id, (
        f"Escape hatch broken: with port_id=None and destination_port="
        f"'SYDNEY', matcher should derive Sydney. Got #{order.port_id}."
    )


# ─── Defensive: stale FK ───────────────────────────────────────


def test_rematch_with_deleted_port_id_falls_through_to_string(
    db, user, geo_fixture
):
    """If order.port_id points to a port that no longer exists in the
    pool (data drift, port row deleted), priority-0 must not return
    None and strand the order. It must fall through to string priorities
    so the order can still get a usable port."""
    sydney = geo_fixture["sydney"]

    # Construct an Order whose port_id refers to a non-existent row.
    # We can't FK-violate via the ORM directly, so seed a Port, refresh,
    # delete it, then construct.
    from sqlalchemy import delete

    ephemeral = Port(name="EPHEMERAL", country_id=geo_fixture["japan"].id)
    db.add(ephemeral)
    db.commit()
    db.refresh(ephemeral)
    ephemeral_id = ephemeral.id
    db.execute(delete(Port).where(Port.id == ephemeral_id))
    db.commit()

    order = _make_order(
        db,
        user_id=user.id,
        destination_port="SYDNEY",
        port_id=ephemeral_id,  # stale
    )

    rematch_order(db, order_id=order.id, user_id=user.id, is_admin=True)
    db.refresh(order)

    # priority-0 finds nothing → falls through → string matches Sydney
    assert order.port_id == sydney.id, (
        f"Defensive fallback failed: stale port_id should fall through "
        f"to string path. Got #{order.port_id}."
    )


# ─── Country: same contract ────────────────────────────────────


def test_rematch_preserves_user_country_override(db, user, geo_fixture):
    """If user changed country_id (e.g. fixing a wrong currency-based
    guess), rematch must not revert it. Same priority-0 contract as
    port, applied to country."""
    australia = geo_fixture["australia"]
    japan = geo_fixture["japan"]

    order = _make_order(
        db,
        user_id=user.id,
        destination_port=None,
        port_id=None,
        country_id=japan.id,  # user corrected
    )
    # Currency hints at Australia, but country_id explicitly says Japan
    order.currency = "AUD"
    db.commit()

    rematch_order(db, order_id=order.id, user_id=user.id, is_admin=True)
    db.refresh(order)

    assert order.country_id == japan.id, (
        f"User-set country Japan (#{japan.id}) was reverted to "
        f"#{order.country_id} (likely Australia from AUD currency). "
        f"geo._match_country priority-0 broken."
    )
    # Sanity that the test is meaningful — Australia exists and AUD
    # would resolve to it without the user override.
    assert australia.id != japan.id


def test_clearing_country_id_lets_currency_re_derive(db, user, geo_fixture):
    """Same escape hatch as port — clearing the override lets the
    matcher re-derive from currency hint."""
    australia = geo_fixture["australia"]

    order = _make_order(
        db,
        user_id=user.id,
        destination_port=None,
        port_id=None,
        country_id=None,  # cleared
    )
    order.currency = "AUD"
    db.commit()

    rematch_order(db, order_id=order.id, user_id=user.id, is_admin=True)
    db.refresh(order)

    assert order.country_id == australia.id, (
        f"With country_id=None and currency=AUD, matcher should "
        f"derive Australia. Got #{order.country_id}."
    )
