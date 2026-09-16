"""Section 5 — inquiry: per-supplier override isolation.

测试目标：
    Pin the per-supplier scoping rule introduced 2026-05-22 after a prod test
    showed that typing `supplier_tel` in supplier A's UI panel poisoned
    supplier B's inquiry too. The root cause was `order.order_metadata`
    being a flat bag — supplier-level keys (supplier_tel/supplier_address/...)
    have no business living at order scope.

    The new layout:
      order_metadata = {
        # per-order keys (shared by all suppliers)
        "po_number": "...", "delivery_address": "...", "ship_name_jp": "...",
        # per-supplier overrides, namespaced
        "supplier_overrides": {
          "1": {"supplier_tel": "..."},
          "2": {"supplier_tel": "...", "payment_method": "..."},
        },
      }

为什么必须有：
    Without this test, anyone refactoring `_order_metadata()` or
    `field_inspection.compute_field_sources()` can silently re-collapse the
    namespace and re-introduce the leak — invisible until a multi-supplier
    customer notices their Excel contains the wrong supplier's contact.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from domains.inquiry.field_inspection import (
    SUPPLIER_LEVEL_KEYS,
    compute_field_sources,
    supplier_overrides_for,
)
from domains.inquiry.models import SupplierTemplate
from domains.inquiry.orchestrator import _order_metadata
from domains.masterdata.models import Country, Port, Supplier
from domains.orders.models import Order


def _zoned() -> dict:
    return {"zones": {"meta": {}, "product_table": {}}}


def _japan_template() -> SupplierTemplate:
    return SupplierTemplate(
        id=99,
        template_name="日本订单标准",
        field_positions={
            "supplier_name": "A3",
            "supplier_tel": "A6",
            "supplier_address": "A5",
            "po_number": "L4",
            "delivery_address": "I8",
        },
        product_table_config={"columns": {}, "start_row": 12},
        template_styles=_zoned(),
        has_product_table=True,
    )


def _seed_two_suppliers(db: Session) -> tuple[int, int]:
    a = Supplier(name="A 食品", phone="111-111", address="A 街 1 号", status=True)
    b = Supplier(name="B 食品", phone="222-222", address="B 街 2 号", status=True)
    db.add_all([a, b])
    db.commit()
    db.refresh(a)
    db.refresh(b)
    return a.id, b.id


def _make_order(**kwargs) -> Order:
    return Order(
        user_id=1,
        filename="t.pdf",
        file_url="orders/t.pdf",
        file_type="pdf",
        status="ready",
        **kwargs,
    )


# ─── Schema sanity ────────────────────────────────────────────


def test_supplier_level_keys_includes_known_supplier_fields() -> None:
    """The canonical list must include the fields that came from Supplier
    master — adding a new supplier_* field key without adding it here
    re-opens the contamination hole."""
    assert "supplier_tel" in SUPPLIER_LEVEL_KEYS
    assert "supplier_address" in SUPPLIER_LEVEL_KEYS
    assert "supplier_fax" in SUPPLIER_LEVEL_KEYS
    assert "supplier_email" in SUPPLIER_LEVEL_KEYS
    assert "supplier_zip_code" in SUPPLIER_LEVEL_KEYS
    assert "payment_method" in SUPPLIER_LEVEL_KEYS
    assert "payment_date" in SUPPLIER_LEVEL_KEYS
    # NOT supplier-level: these are per-order, shared by all suppliers
    assert "delivery_address" not in SUPPLIER_LEVEL_KEYS
    assert "ship_name_jp" not in SUPPLIER_LEVEL_KEYS
    assert "po_number" not in SUPPLIER_LEVEL_KEYS


def test_supplier_overrides_for_handles_missing_keys() -> None:
    """Must gracefully return {} when overrides aren't set yet, accept both
    `int` and `str(int)` supplier-id forms (JSON only preserves string)."""
    assert supplier_overrides_for(None, 1) == {}
    assert supplier_overrides_for({}, 1) == {}
    assert supplier_overrides_for({"supplier_overrides": {"1": {"k": "v"}}}, 1) == {
        "k": "v"
    }
    assert supplier_overrides_for({"supplier_overrides": {1: {"k": "v"}}}, 1) == {
        "k": "v"
    }


# ─── Field inspection: per-supplier isolation ─────────────────


def test_supplier_overrides_show_per_supplier(session_factory) -> None:
    """User typed supplier_tel for supplier A; supplier B's panel must
    still see the master phone, not A's value."""
    db = session_factory()
    try:
        a_id, b_id = _seed_two_suppliers(db)
        order = _make_order(
            order_metadata={
                "po_number": "P1",
                "supplier_overrides": {str(a_id): {"supplier_tel": "AAA-FROM-USER"}},
            }
        )
        # Supplier A: shows the override
        fields_a = compute_field_sources(
            order=order, template=_japan_template(), supplier_id=a_id, db=db
        )
        by_key_a = {f.key: f for f in fields_a}
        assert by_key_a["supplier_tel"].value == "AAA-FROM-USER"
        assert by_key_a["supplier_tel"].source == "metadata"

        # Supplier B: must still show master, NOT supplier A's override.
        fields_b = compute_field_sources(
            order=order, template=_japan_template(), supplier_id=b_id, db=db
        )
        by_key_b = {f.key: f for f in fields_b}
        assert by_key_b["supplier_tel"].value == "222-222"
        assert by_key_b["supplier_tel"].source == "supplier_master"
    finally:
        db.close()


def test_top_level_supplier_key_is_ignored_legacy_safety(session_factory) -> None:
    """Legacy / accidental contamination: if order_metadata.supplier_tel is
    set at top level, BOTH suppliers must ignore it (fall through to
    supplier master), NOT use it. This prevents the prod bug from re-emerging
    even when stale top-level data exists."""
    db = session_factory()
    try:
        a_id, b_id = _seed_two_suppliers(db)
        order = _make_order(
            order_metadata={"supplier_tel": "LEGACY-CONTAMINATION"}
        )
        for sid, expected_phone in [(a_id, "111-111"), (b_id, "222-222")]:
            fields = compute_field_sources(
                order=order, template=_japan_template(), supplier_id=sid, db=db
            )
            by_key = {f.key: f for f in fields}
            assert by_key["supplier_tel"].value == expected_phone, (
                f"supplier {sid} must fall through to master, "
                f"not the top-level legacy value"
            )
            assert by_key["supplier_tel"].source == "supplier_master"
    finally:
        db.close()


def test_order_level_field_is_shared_across_suppliers(session_factory) -> None:
    """delivery_address is per-order — both suppliers see the same value."""
    db = session_factory()
    try:
        a_id, b_id = _seed_two_suppliers(db)
        order = _make_order(
            order_metadata={"delivery_address": "Shared delivery target"}
        )
        for sid in [a_id, b_id]:
            fields = compute_field_sources(
                order=order, template=_japan_template(), supplier_id=sid, db=db
            )
            by_key = {f.key: f for f in fields}
            assert by_key["delivery_address"].value == "Shared delivery target"
            assert by_key["delivery_address"].source == "metadata"
    finally:
        db.close()


# ─── Orchestrator: rendering uses the same precedence ─────────


def test_render_metadata_isolated_per_supplier(session_factory) -> None:
    """`_order_metadata()` must hand each supplier their own contact set;
    user typing for supplier A must not appear in supplier B's render dict."""
    db = session_factory()
    try:
        a_id, b_id = _seed_two_suppliers(db)
        order = _make_order(
            order_metadata={
                "supplier_overrides": {
                    str(a_id): {"supplier_tel": "AAA", "supplier_address": "A address"},
                }
            }
        )
        meta_a = _order_metadata(order, db=db, supplier_id=a_id)
        meta_b = _order_metadata(order, db=db, supplier_id=b_id)
        assert meta_a["supplier_tel"] == "AAA"
        assert meta_a["supplier_address"] == "A address"
        # B must NOT see A's overrides.
        assert meta_b["supplier_tel"] == "222-222"
        assert meta_b["supplier_address"] == "B 街 2 号"
    finally:
        db.close()


def test_render_metadata_ignores_legacy_top_level_supplier_keys(session_factory) -> None:
    """Even if order_metadata.supplier_tel = 'X' exists (legacy contamination),
    rendering pulls from supplier master, not 'X'."""
    db = session_factory()
    try:
        a_id, b_id = _seed_two_suppliers(db)
        order = _make_order(
            order_metadata={"supplier_tel": "LEAKED-FROM-OLDER-BUG"}
        )
        meta_a = _order_metadata(order, db=db, supplier_id=a_id)
        meta_b = _order_metadata(order, db=db, supplier_id=b_id)
        assert meta_a["supplier_tel"] == "111-111"
        assert meta_b["supplier_tel"] == "222-222"
    finally:
        db.close()


def test_supplier_override_beats_supplier_master(session_factory) -> None:
    """If a per-supplier override exists, it overrides the master value
    (which is the whole point of having user-edit capability)."""
    db = session_factory()
    try:
        a_id, _ = _seed_two_suppliers(db)
        order = _make_order(
            order_metadata={
                "supplier_overrides": {str(a_id): {"supplier_tel": "PINNED"}}
            }
        )
        meta = _order_metadata(order, db=db, supplier_id=a_id)
        assert meta["supplier_tel"] == "PINNED"
    finally:
        db.close()
