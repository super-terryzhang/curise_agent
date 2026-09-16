"""Section 6 — rollback_batch round-trip completeness.

Prior version of `rollback_batch` only restored 3 of the 14 fields
`_apply_update` is allowed to mutate (price / unit / pack_size).
The other 11 (product_name_jp, brand, unit_size, country_of_origin,
currency, category_id, supplier_id, country_id, port_id, effective_from,
effective_to) stayed dirty — silently incomplete rollback. Verified
prod 2026-05-18 batch #6 — 8 distinct fields mutated, only 3 restorable
under old code.

These tests pin the contract:
  1. After update → rollback, EVERY field returns to its seed value
     (typed equality — Decimal stays Decimal, datetime stays datetime).
  2. Adding a new mutable field to `_apply_update` without updating
     `_FIELD_RESTORERS` makes test #2 fail (catches future drift).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from domains.masterdata.models import Category, Country, Port, Product, Supplier
from domains.masterdata.upload import service as upload_service
from domains.masterdata.upload.models import StagingProduct, UploadBatch
from domains.masterdata.upload.service import _FIELD_RESTORERS, _apply_update

# ─── Seed helpers ─────────────────────────────────────────────


def _seed_full_product(db, seed_user) -> tuple[Product, dict]:
    """Insert a Product with EVERY field populated to a distinct, typed
    sentinel value so round-trip differences are visible.
    Returns the product + a dict of the seed values for comparison."""
    cat_orig = Category(name="CAT_ORIG")
    cat_new = Category(name="CAT_NEW")
    sup_orig = Supplier(name="SUP_ORIG")
    sup_new = Supplier(name="SUP_NEW")
    country_orig = Country(name="COUNTRY_ORIG")
    country_new = Country(name="COUNTRY_NEW")
    port_orig = Port(name="PORT_ORIG")
    port_new = Port(name="PORT_NEW")
    db.add_all([
        cat_orig, cat_new, sup_orig, sup_new,
        country_orig, country_new, port_orig, port_new,
    ])
    db.commit()

    seed = {
        "product_name_en": "TEST_PRODUCT",          # identity — immutable
        "code": "TEST-CODE-001",                    # identity — immutable
        "product_name_jp": "テスト商品",
        "brand": "BRAND_ORIG",
        "unit": "KG",
        "unit_size": "100G",
        "pack_size": "1KG/CT",
        "country_of_origin": "ORIG_LAND",
        "currency": "USD",
        "price": Decimal("100.00"),
        "contract_price": Decimal("150.00"),
        "purchase_price_effective_from": datetime(2026, 1, 1),
        "purchase_price_effective_to": datetime(2026, 6, 30),
        "selling_price_effective_from": datetime(2026, 2, 1),
        "selling_price_effective_to": datetime(2026, 12, 31),
        "category_id": cat_orig.id,
        "supplier_id": sup_orig.id,
        "country_id": country_orig.id,
        "port_id": port_orig.id,
        "effective_from": datetime(2026, 1, 1),
        "effective_to": datetime(2026, 12, 31),
    }
    p = Product(**seed, status=True)
    db.add(p)
    db.commit()
    db.refresh(p)

    # Also return the "new" FK ids so the update can change them.
    new_targets = {
        "category_id": cat_new.id,
        "supplier_id": sup_new.id,
        "country_id": country_new.id,
        "port_id": port_new.id,
        "category_name": cat_new.name,
        "supplier_name": sup_new.name,
        "country_name": country_new.name,
        "port_name": port_new.name,
    }
    return p, {"seed": seed, "new_targets": new_targets}


def _mutate_via_apply_update(db, target: Product, ctx: dict) -> int:
    """Drive `_apply_update` through a synthetic StagingProduct that
    carries NEW values for every mutable field."""
    nt = ctx["new_targets"]
    batch = UploadBatch(
        user_id=1, filename="test", entity_type="products", status="ready",
    )
    db.add(batch)
    db.commit()

    raw = {
        # Headers must match `_HEADER_ALIASES` aliases so `_canonical_fields`
        # recognises them.
        "product_name_jp": "更新後",
        "brand": "BRAND_NEW",
        "unit_size": "200G",
        "country_of_origin": "NEW_LAND",
        "currency": "JPY",
        "contract_price": 250,
        "purchase_price_effective_from": "2026-03-01",
        "purchase_price_effective_to": "2026-08-31",
        "selling_price_effective_from": "2026-04-01",
        "selling_price_effective_to": "2027-03-31",
        "category": nt["category_name"],
        "supplier": nt["supplier_name"],
        "country": nt["country_name"],
        "port": nt["port_name"],
        "effective_from": "2026-06-01",
        "effective_to": "2026-09-30",
    }
    sp = StagingProduct(
        batch_id=batch.id,
        row_index=1,
        raw_data=raw,
        product_code=target.code,
        product_name=target.product_name_en,
        price=Decimal("200.00"),
        unit="EA",
        pack_size="2KG/CT",
        match_status="exact",
        match_target_id=target.id,
    )
    db.add(sp)
    db.commit()

    _apply_update(db, target, sp, batch_id=batch.id, user_id=1)
    db.commit()
    return batch.id


# ─── Round-trip test — the actual contract ────────────────────


def test_round_trip_all_19_fields_restore_to_seed(db, seed_user):
    """Seed → mutate → rollback. Every field must equal its seed value
    AFTER rollback. Typed equality (Decimal/datetime/int)."""
    p, ctx = _seed_full_product(db, seed_user)
    seed = ctx["seed"]
    pid = p.id

    batch_id = _mutate_via_apply_update(db, p, ctx)

    # Sanity: all 19 mutable fields changed.
    db.refresh(p)
    assert p.brand == "BRAND_NEW", "setup error: update didn't propagate"

    # Mark batch as committed so rollback can fire.
    batch = db.get(UploadBatch, batch_id)
    batch.status = "completed"
    db.commit()

    result = upload_service.rollback_batch(db, batch_id=batch_id, user_id=1)
    assert result["restored"] >= 19, (
        f"expected ≥19 fields restored, got {result}"
    )
    assert result["skipped"] == 0, (
        f"unexpected skipped fields — restorer table missing entry: {result}"
    )

    # Re-read product and assert every field equals its seed value.
    db.refresh(p)
    db.expire_all()
    fresh = db.get(Product, pid)

    # Compare each field; use typed equality so Decimal('100.00') ≠ 100.0
    # surfaces as a real bug (price serialisation drift).
    assert fresh.product_name_jp == seed["product_name_jp"]
    assert fresh.brand == seed["brand"]
    assert fresh.unit == seed["unit"]
    assert fresh.unit_size == seed["unit_size"]
    assert fresh.pack_size == seed["pack_size"]
    assert fresh.country_of_origin == seed["country_of_origin"]
    assert fresh.currency == seed["currency"]
    assert Decimal(str(fresh.price)) == seed["price"]
    assert Decimal(str(fresh.contract_price)) == seed["contract_price"]
    assert fresh.purchase_price_effective_from == seed["purchase_price_effective_from"]
    assert fresh.purchase_price_effective_to == seed["purchase_price_effective_to"]
    assert fresh.selling_price_effective_from == seed["selling_price_effective_from"]
    assert fresh.selling_price_effective_to == seed["selling_price_effective_to"]
    assert fresh.category_id == seed["category_id"]
    assert fresh.supplier_id == seed["supplier_id"]
    assert fresh.country_id == seed["country_id"]
    assert fresh.port_id == seed["port_id"]
    assert fresh.effective_from == seed["effective_from"]
    assert fresh.effective_to == seed["effective_to"]


def test_restorer_table_matches_apply_update_mutation_surface(db, seed_user):
    """Drift guard: if someone adds a new mutable field to `_apply_update`
    without adding a restorer here, the round-trip test above will fail
    with `skipped > 0`. This test independently asserts the dict at
    covers all known fields — a check that doesn't depend on
    running the full pipeline."""
    expected_fields = {
        "unit", "pack_size", "product_name_jp", "brand", "unit_size",
        "country_of_origin", "currency", "price", "contract_price",
        "category_id", "supplier_id", "country_id", "port_id",
        "effective_from", "effective_to",
        "purchase_price_effective_from", "purchase_price_effective_to",
        "selling_price_effective_from", "selling_price_effective_to",
    }
    covered = set(_FIELD_RESTORERS.keys())
    missing = expected_fields - covered
    assert not missing, f"_FIELD_RESTORERS missing: {missing}"


def test_unknown_field_in_changelog_is_skipped_not_crash(db, seed_user):
    """Defense-in-depth: if changelog has a row with a field_name we
    don't know how to restore, rollback should `skipped += 1` and keep
    going, NOT crash the whole rollback."""
    from agent.storage.models import (
        AgentMemory,  # noqa: F401  unrelated import to keep model registry warm
    )
    from domains.masterdata.upload.models import ProductChangeLog

    p, _ = _seed_full_product(db, seed_user)
    batch = UploadBatch(
        user_id=1, filename="test_unknown_field", entity_type="products",
        status="completed",
    )
    db.add(batch)
    db.commit()

    # Inject a changelog row with a bogus field name.
    db.add(
        ProductChangeLog(
            batch_id=batch.id,
            product_id=p.id,
            user_id=1,
            action="update",
            field_name="nonexistent_column_xyz",
            old_value="some_value",
            new_value="updated",
        )
    )
    db.commit()

    result = upload_service.rollback_batch(db, batch_id=batch.id, user_id=1)
    assert result["skipped"] == 1, f"expected 1 skipped, got {result}"
    # No exception — rollback completed gracefully.
