"""Product availability is the explicit status switch, not a date window."""

from __future__ import annotations

from datetime import datetime, timedelta

from domains.masterdata._products_service import _is_effective, serialize
from domains.masterdata.models import Category, Country, Product, Supplier


def test_product_model_has_no_obsolete_validity_columns():
    assert "effective_from" not in Product.__table__.c
    assert "effective_to" not in Product.__table__.c


def test_is_effective_mirrors_status_even_if_a_stale_object_has_old_attributes():
    enabled = Product(product_name_en="enabled", status=True)
    disabled = Product(product_name_en="disabled", status=False)
    # A stale caller/object must not revive the removed date semantics.
    enabled.effective_to = datetime.utcnow() - timedelta(days=30)
    disabled.effective_to = datetime.utcnow() + timedelta(days=30)

    assert _is_effective(enabled) is True
    assert _is_effective(disabled) is False


def test_serialize_uses_status_and_omits_obsolete_validity_fields(db):
    country = Country(name="JP", code="JP")
    category = Category(name="Frozen")
    supplier = Supplier(name="S1")
    db.add_all([country, category, supplier])
    db.flush()
    product = Product(
        product_name_en="Fresh Beef",
        code="FB-001",
        country_id=country.id,
        category_id=category.id,
        supplier_id=supplier.id,
        status=True,
    )
    db.add(product)
    db.commit()

    body = serialize(db, product)

    assert body["status"] is True
    assert body["is_effective"] is True
    assert "effective_from" not in body
    assert "effective_to" not in body
