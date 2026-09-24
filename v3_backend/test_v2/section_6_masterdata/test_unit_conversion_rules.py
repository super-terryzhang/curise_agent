"""Database guarantees for reusable unit-conversion rules."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from domains.masterdata.models import Product, UnitConversionRule


def make_rule(**overrides) -> UnitConversionRule:
    values = {
        "scope_type": "source_unit",
        "product_id": None,
        "source_system": "oracle",
        "source_unit": "KG2.2",
        "target_unit": "KG",
        "source_quantity": Decimal("1"),
        "target_quantity": Decimal("1"),
        "target_step": None,
        "break_pack": None,
        "pack_signature": None,
        "status": "draft",
        "evidence": "PO165047CCI exact unit and price comparison",
        "valid_from": None,
        "valid_to": None,
        "verified_by": None,
        "verified_at": None,
        "revision": 1,
        "created_by": 1,
        "updated_by": 1,
    }
    values.update(overrides)
    return UnitConversionRule(**values)


def make_product(db) -> Product:
    product = Product(product_name_en="Conversion product", unit="CT", status=True)
    db.add(product)
    db.flush()
    return product


def assert_rejected(db, rule: UnitConversionRule) -> None:
    db.add(rule)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


@pytest.mark.parametrize(
    "overrides",
    [
        {"scope_type": "global"},
        {"status": "approved"},
        {"source_quantity": Decimal("0")},
        {"target_quantity": Decimal("-1")},
        {"target_step": Decimal("0")},
        {"valid_from": date(2026, 9, 24), "valid_to": date(2026, 9, 23)},
        {
            "status": "verified",
            "verified_by": None,
            "verified_at": datetime(2026, 9, 24, 10, 0),
        },
        {
            "status": "verified",
            "verified_by": 1,
            "verified_at": None,
        },
    ],
)
def test_database_rejects_invalid_rule_values(db, seed_user, overrides):
    """Removing a schema check would allow an unusable rule to persist."""

    assert_rejected(db, make_rule(**overrides))


def test_database_rejects_non_positive_revision(db, seed_user):
    """The database check still protects non-ORM writers from revision zero."""

    values = {
        column.name: getattr(make_rule(), column.name)
        for column in UnitConversionRule.__table__.columns
        if column.name not in {"id", "created_at", "updated_at"}
    }
    values["revision"] = 0
    with pytest.raises(IntegrityError):
        db.execute(UnitConversionRule.__table__.insert().values(**values))
        db.commit()
    db.rollback()


def test_scope_requires_product_only_for_product_rules(db, seed_user):
    """Changing the scope/product check would make rule selection ambiguous."""

    product = make_product(db)
    assert_rejected(db, make_rule(product_id=product.id))
    assert_rejected(db, make_rule(scope_type="product", product_id=None))

    valid = make_rule(
        scope_type="product",
        product_id=product.id,
        source_unit="CA2.27",
        target_unit="CT",
        pack_signature="ct|86gx12|",
    )
    db.add(valid)
    db.commit()

    assert valid.id is not None


def test_verified_source_scope_is_unique_but_drafts_can_coexist(db, seed_user):
    """Dropping the partial unique index would let query order choose a rule."""

    verified = {
        "status": "verified",
        "verified_by": seed_user.id,
        "verified_at": datetime(2026, 9, 24, 10, 0),
    }
    db.add(make_rule(**verified))
    db.commit()

    assert_rejected(db, make_rule(**verified))

    db.add_all([make_rule(), make_rule()])
    db.commit()
    assert db.query(UnitConversionRule).count() == 3


def test_verified_product_scope_is_unique_per_pack_fingerprint(db, seed_user):
    """A duplicate verified product-pack rule must be rejected at the DB boundary."""

    product = make_product(db)
    values = {
        "scope_type": "product",
        "product_id": product.id,
        "source_unit": "CA2.27",
        "target_unit": "CT",
        "pack_signature": "ct|86gx12|",
        "status": "verified",
        "verified_by": seed_user.id,
        "verified_at": datetime(2026, 9, 24, 10, 0),
    }
    db.add(make_rule(**values))
    db.commit()

    assert_rejected(db, make_rule(**values))

    different_pack = make_rule(**{**values, "pack_signature": "ct|100gx12|"})
    db.add(different_pack)
    db.commit()
    assert different_pack.id is not None
