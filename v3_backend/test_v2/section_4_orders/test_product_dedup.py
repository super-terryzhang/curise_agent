"""Order projection preserves source rows, including repeated product codes.

Oracle source coverage validation handles extraction duplicates; SKU equality
alone cannot distinguish repeated demand from a duplicate extraction.
"""

from __future__ import annotations

import pytest

from domains.document.models import Document
from domains.identity.models import User
from domains.orders.projection import project_purchase_order
from infrastructure.security import hash_password


@pytest.fixture
def user(db) -> User:
    u = User(
        email="dedup@x.test",
        hashed_password=hash_password("p"),
        full_name="Dedup Tester",
        role="employee",
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _make_doc(db, user, products: list[dict]) -> Document:
    doc = Document(
        user_id=user.id,
        filename="t.pdf",
        file_type="pdf",
        file_url="documents/t.pdf",
        doc_type="purchase_order",
        status="extracted",
        extracted_data={"products": products, "metadata": {"po_number": "TEST"}},
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def test_repeated_product_codes_are_preserved(db, user):
    """The SKU is not a line identity; equal codes alone cannot justify dropping a row."""
    doc = _make_doc(db, user, [
        {"product_code": "AAA-001", "product_name": "Apple", "quantity": 10},
        {"product_code": "BBB-002", "product_name": "Banana", "quantity": 5},
        {"product_code": "AAA-001", "product_name": "Apple (again)", "quantity": 10},
    ])
    order = project_purchase_order(doc, db)
    assert order.product_count == 3
    codes = [p["product_code"] for p in order.products]
    assert codes == ["AAA-001", "BBB-002", "AAA-001"]


def test_repeated_code_keeps_each_rows_quantity_and_metadata(db, user):
    """Different demand rows retain their independent quantities and prices."""
    doc = _make_doc(db, user, [
        {"product_code": "X1", "product_name": "First", "quantity": 1},
        {"product_code": "X1", "product_name": "Second", "quantity": 999, "unit_price": 5.0},
    ])
    order = project_purchase_order(doc, db)
    assert order.product_count == 2
    assert order.products[0]["product_name"] == "First"
    assert order.products[0]["quantity"] == 1
    assert order.products[1]["quantity"] == 999
    assert order.products[1]["unit_price"] == 5.0
    # Do not transfer fields between rows.
    assert "unit_price" not in order.products[0]


def test_dedup_keeps_items_without_codes(db, user):
    """Products without product_code can't be deduplicated reliably — keep all."""
    doc = _make_doc(db, user, [
        {"product_name": "Unknown 1"},  # no code
        {"product_code": "ABC", "product_name": "Coded"},
        {"product_name": "Unknown 2"},  # no code
        {"product_code": None, "product_name": "Explicit null code"},
        {"product_code": "", "product_name": "Empty code"},
    ])
    order = project_purchase_order(doc, db)
    # All 4 non-code items + 1 coded item kept
    assert order.product_count == 5


def test_dedup_no_op_when_no_duplicates(db, user):
    """All unique codes → list unchanged."""
    products = [
        {"product_code": f"P{i:03d}", "product_name": f"Item {i}", "quantity": i}
        for i in range(1, 11)
    ]
    doc = _make_doc(db, user, products)
    order = project_purchase_order(doc, db)
    assert order.product_count == 10
    assert [p["product_code"] for p in order.products] == [f"P{i:03d}" for i in range(1, 11)]


def test_dedup_handles_non_dict_entries_gracefully(db, user):
    """Malformed entries (string, None) get filtered out."""
    doc = _make_doc(db, user, [
        {"product_code": "OK1", "product_name": "Valid"},
        "this should not be here",  # not a dict
        None,
        {"product_code": "OK2", "product_name": "Also valid"},
    ])
    order = project_purchase_order(doc, db)
    assert order.product_count == 2
    assert {p["product_code"] for p in order.products} == {"OK1", "OK2"}
