"""Order matching integration for reusable unit-conversion rules."""

from datetime import date, datetime
from decimal import Decimal

from domains.masterdata.models import Product, UnitConversionRule
from domains.orders.anomaly import findings_for_order_row
from domains.orders.matching import unit_conversion
from domains.orders.models import Order


def make_order(rows: list[dict]) -> Order:
    return Order(
        user_id=1,
        filename="unit.pdf",
        status="matching",
        products=rows,
        delivery_date="2026-09-23",
    )


def make_result(row: dict, product: Product) -> dict:
    return {
        **row,
        "match_status": "matched",
        "match_score": 1.0,
        "match_reason": "产品代码完全匹配",
        "matched_product": {
            "id": product.id,
            "code": product.code,
            "supplier_id": product.supplier_id,
            "unit": product.unit,
            "unit_size": product.unit_size,
            "pack_size": product.pack_size,
        },
    }


def add_product(db, *, code: str, unit: str, unit_size=None) -> Product:
    product = Product(
        product_name_en=code,
        code=code,
        unit=unit,
        unit_size=unit_size,
        supplier_id=1,
        status=True,
    )
    db.add(product)
    db.flush()
    return product


def add_source_rule(db, *, source_unit: str, target_unit: str) -> UnitConversionRule:
    rule = UnitConversionRule(
        scope_type="source_unit",
        source_system="oracle",
        source_unit=source_unit,
        target_unit=target_unit,
        source_quantity=Decimal("1"),
        target_quantity=Decimal("1"),
        status="verified",
        evidence="confirmed",
        verified_by=1,
        verified_at=datetime(2026, 9, 24, 10, 0),
        created_by=1,
        updated_by=1,
    )
    db.add(rule)
    db.commit()
    return rule


def test_disabled_feature_keeps_existing_unit_warning(db, monkeypatch):
    """Removing the flag gate would make rollback unable to restore old behavior."""

    product = add_product(db, code="P1", unit="KG")
    row = {"line_id": "L1", "product_code": "P1", "quantity": 2, "unit": "KG2.2"}
    result = make_result(row, product)
    monkeypatch.setattr(unit_conversion.settings, "UNIT_CONVERSION_RULES_ENABLED", False)

    unit_conversion.apply_verified_unit_conversions(
        make_order([row]), db, [result], date(2026, 9, 23)
    )

    assert "rfq_quantity" not in result
    codes = {item["code"] for item in findings_for_order_row(result)}
    assert "UNIT_CONVERSION_REQUIRED" in codes


def test_verified_source_rule_adds_json_safe_rfq_snapshot(db, monkeypatch):
    """Omitting the coordinator would leave a verified repeat unit unresolved."""

    product = add_product(db, code="P1", unit="KG")
    rule = add_source_rule(db, source_unit="KG2.2", target_unit="KG")
    row = {"line_id": "L1", "product_code": "P1", "quantity": 12, "unit": "KG2.2"}
    result = make_result(row, product)
    monkeypatch.setattr(unit_conversion.settings, "UNIT_CONVERSION_RULES_ENABLED", True)

    unit_conversion.apply_verified_unit_conversions(
        make_order([row]), db, [result], date(2026, 9, 23)
    )

    assert result["source_quantity"] == 12
    assert result["source_unit"] == "KG2.2"
    assert result["rfq_quantity"] == 12
    assert result["rfq_unit"] == "KG"
    assert result["conversion_evidence"]["rule_id"] == rule.id
    assert result["conversion_evidence"]["source_unit"] == "KG2.2"
    assert result["conversion_evidence"]["target_unit"] == "KG"
    assert "unit_conversion_issue" not in result
    assert "UNIT_CONVERSION_REQUIRED" not in {
        item["code"] for item in findings_for_order_row(result)
    }


def test_existing_manual_row_decision_wins_over_reusable_rule(db, monkeypatch):
    """Re-running matching must not overwrite an explicit one-row decision."""

    product = add_product(db, code="P1", unit="CT")
    add_source_rule(db, source_unit="EA", target_unit="CT")
    row = {"line_id": "L1", "product_code": "P1", "quantity": 10, "unit": "EA"}
    result = {
        **make_result(row, product),
        "source_quantity": 10,
        "source_unit": "EA",
        "rfq_quantity": 2,
        "rfq_unit": "CT",
        "conversion_evidence": {
            "verified": True,
            "scope_type": "order_row",
            "evidence": "人工确认 5 EA = 1 CT",
        },
    }
    monkeypatch.setattr(unit_conversion.settings, "UNIT_CONVERSION_RULES_ENABLED", True)

    unit_conversion.apply_verified_unit_conversions(
        make_order([row]), db, [result], date(2026, 9, 23)
    )

    assert result["rfq_quantity"] == 2
    assert result["conversion_evidence"]["scope_type"] == "order_row"


def test_changed_pack_keeps_specific_stale_issue_for_anomaly_ui(db, monkeypatch):
    """Replacing a stale issue with a generic warning would hide the needed action."""

    product = add_product(db, code="P1", unit="CT", unit_size="100gX12")
    db.add(
        UnitConversionRule(
            scope_type="product",
            product_id=product.id,
            source_system="oracle",
            source_unit="CA2.27",
            target_unit="CT",
            source_quantity=Decimal("1"),
            target_quantity=Decimal("1"),
            pack_signature='["CT","86GX12",""]',
            status="verified",
            evidence="old pack",
            verified_by=1,
            verified_at=datetime(2026, 9, 24, 10, 0),
            created_by=1,
            updated_by=1,
        )
    )
    db.commit()
    row = {"line_id": "L1", "product_code": "P1", "quantity": 12, "unit": "CA2.27"}
    result = make_result(row, product)
    monkeypatch.setattr(unit_conversion.settings, "UNIT_CONVERSION_RULES_ENABLED", True)

    unit_conversion.apply_verified_unit_conversions(
        make_order([row]), db, [result], date(2026, 9, 23)
    )
    findings = findings_for_order_row(result)

    assert result["unit_conversion_issue"]["code"] == "UNIT_CONVERSION_RULE_STALE"
    assert [item["code"] for item in findings].count("UNIT_CONVERSION_RULE_STALE") == 1
    assert "UNIT_CONVERSION_REQUIRED" not in {item["code"] for item in findings}


def test_conflict_code_is_preserved_for_user_resolution(db, monkeypatch):
    """The order layer must not collapse evaluator conflicts into a generic code."""

    product = add_product(db, code="P1", unit="KG")
    row = {"line_id": "L1", "product_code": "P1", "quantity": 1, "unit": "KG2.2"}
    result = make_result(row, product)
    monkeypatch.setattr(unit_conversion.settings, "UNIT_CONVERSION_RULES_ENABLED", True)
    monkeypatch.setattr(
        unit_conversion.masterdata_service,
        "evaluate_unit_conversion",
        lambda *_args, **_kwargs: {
            "status": "review",
            "issue_code": "UNIT_CONVERSION_RULE_CONFLICT",
            "message": "存在多条规则",
            "details": {"rule_ids": [1, 2]},
        },
    )

    unit_conversion.apply_verified_unit_conversions(
        make_order([row]), db, [result], date(2026, 9, 23)
    )

    assert result["unit_conversion_issue"]["code"] == "UNIT_CONVERSION_RULE_CONFLICT"


def test_one_evaluator_exception_does_not_stop_safe_sibling(db, monkeypatch):
    """Removing per-row isolation would let one malformed row stop the order scan."""

    bad_product = add_product(db, code="BAD", unit="KG")
    good_product = add_product(db, code="GOOD", unit="KG")
    add_source_rule(db, source_unit="KG2.2", target_unit="KG")
    rows = [
        {"line_id": "L1", "product_code": "BAD", "quantity": 1, "unit": "BROKEN"},
        {"line_id": "L2", "product_code": "GOOD", "quantity": 2, "unit": "KG2.2"},
    ]
    results = [make_result(rows[0], bad_product), make_result(rows[1], good_product)]
    real_evaluator = unit_conversion.masterdata_service.evaluate_unit_conversion

    def fail_one(*args, **kwargs):
        if kwargs["source_unit"] == "BROKEN":
            raise RuntimeError("bad row")
        return real_evaluator(*args, **kwargs)

    monkeypatch.setattr(unit_conversion.settings, "UNIT_CONVERSION_RULES_ENABLED", True)
    monkeypatch.setattr(unit_conversion.masterdata_service, "evaluate_unit_conversion", fail_one)

    unit_conversion.apply_verified_unit_conversions(
        make_order(rows), db, results, date(2026, 9, 23)
    )

    assert results[0]["unit_conversion_issue"]["code"] == "UNIT_CONVERSION_EVALUATION_FAILED"
    assert results[1]["rfq_quantity"] == 2

