"""Behavioral tests for reusable unit conversion evaluation and lifecycle."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from domains.masterdata.errors import Conflict
from domains.masterdata.models import Product, UnitConversionRule
from domains.masterdata.schemas import (
    UnitConversionRuleCreate,
    UnitConversionRuleRetire,
    UnitConversionRuleVerify,
)
from domains.masterdata.service import (
    create_unit_conversion_rule,
    evaluate_unit_conversion,
    normalize_unit,
    product_pack_signature,
    retire_unit_conversion_rule,
    verify_unit_conversion_rule,
)


def add_product(db, *, unit="CT", unit_size="86gX12", pack_size=None) -> Product:
    product = Product(
        product_name_en="Conversion product",
        unit=unit,
        unit_size=unit_size,
        pack_size=pack_size,
        status=True,
    )
    db.add(product)
    db.flush()
    return product


def add_rule(db, **overrides) -> UnitConversionRule:
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
        "status": "verified",
        "evidence": "confirmed against source data",
        "valid_from": None,
        "valid_to": None,
        "verified_by": 1,
        "verified_at": datetime(2026, 9, 24, 10, 0),
        "revision": 1,
        "created_by": 1,
        "updated_by": 1,
    }
    values.update(overrides)
    rule = UnitConversionRule(**values)
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def evaluate(db, **overrides):
    values = {
        "product_id": None,
        "source_system": "oracle",
        "source_quantity": Decimal("12"),
        "source_unit": "KG2.2",
        "target_unit": "KG",
        "business_date": date(2026, 9, 23),
        "product_unit": "KG",
        "product_unit_size": None,
        "product_pack_size": None,
    }
    values.update(overrides)
    return evaluate_unit_conversion(db, **values)


def test_normalization_is_unicode_safe_without_changing_raw_result(db):
    """Removing NFKC normalization would miss full-width unit strings."""

    assert normalize_unit("  ｋｇ  ") == "KG"
    assert product_pack_signature("ＣＴ", "86gX12", None) == '["CT","86GX12",""]'

    result = evaluate(
        db,
        source_quantity="2.20",
        source_unit=" ｋｇ ",
        target_unit="KG",
    )

    assert result["status"] == "same"
    assert result["source_unit"] == " ｋｇ "
    assert result["source_quantity"] == Decimal("2.20")
    assert result["target_quantity"] == Decimal("2.20")
    assert result["target_unit"] == "KG"


def test_standard_mass_conversion_uses_decimal_constant(db):
    """Replacing the mass map with a float would change the exact LB result."""

    result = evaluate(
        db,
        source_quantity=Decimal("10"),
        source_unit="LB",
        target_unit="KG",
    )

    assert result["status"] == "converted"
    assert result["target_quantity"] == Decimal("4.53592370")
    assert result["conversion_evidence"]["scope_type"] == "standard"
    assert result["conversion_evidence"]["verified"] is True


def test_product_rule_precedes_source_rule_and_returns_audited_snapshot(db):
    """Reversing precedence would silently apply a broad rule to a specific pack."""

    product = add_product(db)
    signature = '["CT","86GX12",""]'
    add_rule(db, source_unit="CA2.27", target_unit="CT", target_quantity=Decimal("2"))
    product_rule = add_rule(
        db,
        scope_type="product",
        product_id=product.id,
        source_unit="CA2.27",
        target_unit="CT",
        pack_signature=signature,
        evidence="same supplier pack confirmed",
    )

    result = evaluate(
        db,
        product_id=product.id,
        source_unit="CA2.27",
        target_unit="CT",
        product_unit="CT",
        product_unit_size="86gX12",
    )

    assert result == {
        "status": "converted",
        "source_quantity": Decimal("12"),
        "source_unit": "CA2.27",
        "target_quantity": Decimal("12.0000000000"),
        "target_unit": "CT",
        "conversion_evidence": {
            "verified": True,
            "rule_id": product_rule.id,
            "rule_revision": 1,
            "scope_type": "product",
            "source_quantity": "1.0000000000",
            "target_quantity": "1.0000000000",
            "pack_signature": signature,
            "evidence": "same supplier pack confirmed",
            "verified_by": 1,
            "verified_at": "2026-09-24T10:00:00",
        },
    }


def test_source_rule_is_used_when_no_product_rule_exists(db):
    """Removing source-rule fallback would leave repeat KG2.2 rows unresolved."""

    rule = add_rule(db)

    result = evaluate(db)

    assert result["status"] == "converted"
    assert result["target_quantity"] == Decimal("12.0000000000")
    assert result["conversion_evidence"]["rule_id"] == rule.id
    assert result["conversion_evidence"]["scope_type"] == "source_unit"


def test_draft_and_out_of_period_rules_do_not_convert(db):
    """Relaxing status/date filters would apply unapproved or invalid-period rules."""

    add_rule(db, status="draft", verified_by=None, verified_at=None)
    result = evaluate(db)
    assert result["status"] == "review"
    assert result["issue_code"] == "UNIT_CONVERSION_REQUIRED"

    db.query(UnitConversionRule).delete()
    db.commit()
    add_rule(db, valid_to=date(2026, 9, 22))
    assert evaluate(db)["issue_code"] == "UNIT_CONVERSION_REQUIRED"

    db.query(UnitConversionRule).delete()
    db.commit()
    add_rule(db, valid_from=date(2026, 9, 24))
    assert evaluate(db)["issue_code"] == "UNIT_CONVERSION_REQUIRED"


def test_dated_rule_never_matches_when_business_date_is_missing(db):
    """Falling back to today's date would make historical results non-deterministic."""

    add_rule(db, valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31))

    result = evaluate(db, business_date=None)

    assert result["status"] == "review"
    assert result["issue_code"] == "UNIT_CONVERSION_REQUIRED"


def test_changed_product_pack_is_reported_as_stale(db):
    """Ignoring the saved fingerprint would reuse a rule after packaging changes."""

    product = add_product(db)
    add_rule(
        db,
        scope_type="product",
        product_id=product.id,
        source_unit="CA2.27",
        target_unit="CT",
        pack_signature='["CT","86GX12",""]',
    )

    result = evaluate(
        db,
        product_id=product.id,
        source_unit="CA2.27",
        target_unit="CT",
        product_unit="CT",
        product_unit_size="100gX12",
    )

    assert result["status"] == "review"
    assert result["issue_code"] == "UNIT_CONVERSION_RULE_STALE"
    assert result["details"]["saved_pack_signature"] == '["CT","86GX12",""]'
    assert result["details"]["current_pack_signature"] == '["CT","100GX12",""]'


def test_multiple_eligible_drafts_are_a_conflict_in_shadow_mode(db):
    """Choosing the first candidate would make behavior depend on database order."""

    add_rule(db, status="draft", verified_by=None, verified_at=None)
    add_rule(db, status="draft", verified_by=None, verified_at=None)

    result = evaluate(db, include_drafts=True)

    assert result["status"] == "review"
    assert result["issue_code"] == "UNIT_CONVERSION_RULE_CONFLICT"


def test_non_positive_quantity_is_rejected_without_querying_a_rule(db):
    """Allowing zero/negative source quantities would create invalid RFQ rows."""

    for value in (Decimal("0"), Decimal("-1"), "not-a-number"):
        result = evaluate(db, source_quantity=value)
        assert result["status"] == "review"
        assert result["issue_code"] == "QUANTITY_INVALID"


def test_target_step_blocks_fractional_package_without_rounding(db):
    """Rounding 1/3 CT would order a quantity the rule never authorized."""

    add_rule(
        db,
        source_quantity=Decimal("3"),
        target_quantity=Decimal("1"),
        target_step=Decimal("1"),
    )

    result = evaluate(db, source_quantity=Decimal("1"))

    assert result["status"] == "review"
    assert result["issue_code"] == "NON_INTEGER_PACKAGE_QUANTITY"
    assert result["details"]["calculated_quantity"].startswith("0.3333333333")
    assert result["details"]["target_step"] == "1.0000000000"


def test_unknown_break_pack_does_not_block_quantity_that_meets_known_step(db):
    """Treating NULL break_pack as false would create a needless anomaly."""

    add_rule(db, target_step=Decimal("1"), break_pack=None)

    result = evaluate(db, source_quantity=Decimal("12"))

    assert result["status"] == "converted"
    assert result["target_quantity"] == Decimal("12.0000000000")


def test_rule_lifecycle_is_draft_verify_retire_with_optimistic_lock(db, seed_user):
    """Skipping explicit verification or revision checks would make rules unaudited."""

    created = create_unit_conversion_rule(
        db,
        UnitConversionRuleCreate(
            scope_type="source_unit",
            source_system="oracle",
            source_unit=" ca22.0 ",
            target_unit="ca",
            source_quantity=Decimal("1"),
            target_quantity=Decimal("1"),
            evidence="PO sample checked",
        ),
        actor_id=seed_user.id,
    )
    assert created["status"] == "draft"
    assert created["source_unit"] == "CA22.0"
    assert created["revision"] == 1

    verified = verify_unit_conversion_rule(
        db,
        created["id"],
        UnitConversionRuleVerify(
            expected_revision=1,
            evidence="Confirmed against supplier unit",
        ),
        actor_id=seed_user.id,
    )
    assert verified["status"] == "verified"
    assert verified["revision"] == 2
    assert verified["verified_by"] == seed_user.id

    with pytest.raises(Conflict, match="已被其他操作修改"):
        retire_unit_conversion_rule(
            db,
            created["id"],
            UnitConversionRuleRetire(expected_revision=1, evidence="obsolete"),
            actor_id=seed_user.id,
        )

    retired = retire_unit_conversion_rule(
        db,
        created["id"],
        UnitConversionRuleRetire(expected_revision=2, evidence="supplier changed"),
        actor_id=seed_user.id,
    )
    assert retired["status"] == "retired"
    assert retired["revision"] == 3
    assert retired["evidence"] == "supplier changed"


def test_verifying_duplicate_scope_returns_domain_conflict(db, seed_user):
    """Leaking IntegrityError would bypass the HTTP domain error contract."""

    add_rule(db)
    duplicate = create_unit_conversion_rule(
        db,
        UnitConversionRuleCreate(
            scope_type="source_unit",
            source_system="oracle",
            source_unit="KG2.2",
            target_unit="KG",
            source_quantity=Decimal("1"),
            target_quantity=Decimal("1"),
            evidence="second candidate",
        ),
        actor_id=seed_user.id,
    )

    with pytest.raises(Conflict, match="已有已验证规则"):
        verify_unit_conversion_rule(
            db,
            duplicate["id"],
            UnitConversionRuleVerify(
                expected_revision=1,
                evidence="duplicate confirmation",
            ),
            actor_id=seed_user.id,
        )
