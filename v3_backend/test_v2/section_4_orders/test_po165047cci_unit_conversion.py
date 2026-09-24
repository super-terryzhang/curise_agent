"""Anonymized acceptance boundary from the production PO165047CCI audit."""

import json
from collections import Counter
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from domains.masterdata.models import Product, UnitConversionRule
from domains.masterdata.service import product_pack_signature
from domains.orders.models import Order
from scripts.audit_unit_conversions import _set_transaction_read_only, audit_order

FIXTURE = Path(__file__).parent / "fixtures" / "po165047cci_units.json"


def load_fixture():
    return json.loads(FIXTURE.read_text())


def seed_order_and_rules(db):
    rows = load_fixture()
    products = []
    results = []
    product_specific_id = None
    for row in rows:
        source = {
            "line_id": f"L{row['row']}",
            "product_code": row["product_code"],
            "product_name": f"Synthetic {row['row']}",
            "quantity": row["source_quantity"],
            "unit": row["source_unit"],
        }
        products.append(source)
        if row["match_status"] != "matched":
            results.append(
                {
                    **source,
                    "match_status": "not_matched",
                    "match_score": 0,
                    "match_reason": "fixture unmatched",
                    "matched_product": None,
                }
            )
            continue
        product = Product(
            product_name_en=f"Synthetic {row['row']}",
            code=row["product_code"],
            unit=row["product_unit"],
            unit_size=row["unit_size"],
            pack_size=row["pack_size"],
            status=True,
        )
        db.add(product)
        db.flush()
        if row["product_specific"]:
            product_specific_id = product.id
        results.append(
            {
                **source,
                "match_status": "matched",
                "match_score": 1.0,
                "matched_product": {
                    "id": product.id,
                    "code": product.code,
                    "unit": product.unit,
                    "unit_size": product.unit_size,
                    "pack_size": product.pack_size,
                },
            }
        )

    for source_unit, target_unit in (
        ("KG2.2", "KG"),
        ("CA22.0", "CA"),
        ("CA13.22", "CA"),
        ("CA15.0", "CA"),
    ):
        db.add(
            UnitConversionRule(
                scope_type="source_unit",
                source_system="oracle",
                source_unit=source_unit,
                target_unit=target_unit,
                source_quantity=Decimal("1"),
                target_quantity=Decimal("1"),
                status="verified",
                evidence="PO165047CCI anonymized acceptance",
                verified_by=1,
                verified_at=datetime(2026, 9, 24, 10, 0),
                created_by=1,
                updated_by=1,
            )
        )
    product_rule = UnitConversionRule(
        scope_type="product",
        product_id=product_specific_id,
        source_system="oracle",
        source_unit="CA2.27",
        target_unit="CT",
        source_quantity=Decimal("1"),
        target_quantity=Decimal("1"),
        pack_signature=product_pack_signature("CT", "86gX12", None),
        status="verified",
        evidence="same supplier pack confirmed",
        verified_by=1,
        verified_at=datetime(2026, 9, 24, 10, 0),
        created_by=1,
        updated_by=1,
    )
    db.add(product_rule)
    order = Order(
        user_id=1,
        filename="po165047cci-anonymized.json",
        po_number="PO165047CCI",
        delivery_date="2026-09-23",
        products=products,
        match_results=results,
    )
    db.add(order)
    db.commit()
    return order, product_rule


def test_fixture_preserves_the_production_distribution():
    """Changing the fixture counts would stop testing the audited PO boundary."""

    rows = load_fixture()
    matched = [row for row in rows if row["match_status"] == "matched"]
    pairs = Counter((row["source_unit"], row["target_unit"]) for row in matched)

    assert len(rows) == 56
    assert len(matched) == 53
    assert len(rows) - len(matched) == 3
    assert pairs == Counter(
        {
            ("KG2.2", "KG"): 46,
            ("CA22.0", "CA"): 4,
            ("CA13.22", "CA"): 1,
            ("CA15.0", "CA"): 1,
            ("CA2.27", "CT"): 1,
        }
    )


def test_all_five_verified_rules_convert_53_rows_without_mutating_source(db):
    """A regression in precedence or raw-row handling would change this exact result."""

    order, _product_rule = seed_order_and_rules(db)
    original = deepcopy(order.products)

    result = audit_order(db, order, include_drafts=False)

    assert result["total_rows"] == 56
    assert result["matched_rows"] == 53
    assert result["unmatched_rows"] == 3
    assert result["converted_rows"] == 53
    assert result["review_rows"] == 0
    assert result["issue_counts"] == {"PRODUCT_NOT_MATCHED": 3}
    row_12 = next(row for row in result["rows"] if row["row_index"] == 12)
    assert row_12["target_quantity"] == "12.0000000000"
    assert row_12["target_unit"] == "CT"
    assert order.products == original


def test_unverified_product_rule_leaves_one_review_and_52_safe_conversions(db):
    """Falling back to a guessed broad rule would incorrectly clear row 12."""

    order, product_rule = seed_order_and_rules(db)
    product_rule.status = "retired"
    db.commit()

    result = audit_order(db, order, include_drafts=False)

    assert result["converted_rows"] == 52
    assert result["review_rows"] == 1
    assert result["issue_counts"] == {
        "PRODUCT_NOT_MATCHED": 3,
        "UNIT_CONVERSION_REQUIRED": 1,
    }


def test_postgres_audit_explicitly_marks_the_transaction_read_only():
    """Removing SET TRANSACTION READ ONLY would turn an audit into a write risk."""

    statements = []
    fake_db = SimpleNamespace(
        bind=SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
        execute=lambda statement: statements.append(str(statement)),
    )

    _set_transaction_read_only(fake_db)

    assert statements == ["SET TRANSACTION READ ONLY"]
