"""Inquiry preflight accepts audited rule snapshots from order matching."""

from types import SimpleNamespace

from domains.inquiry.automation import prepare_inquiry
from domains.inquiry.models import SupplierTemplate
from domains.masterdata.models import Product, Supplier
from domains.orders.models import Order


def setup_inquiry_row(db, *, verified: bool):
    supplier = Supplier(name="Supplier", status=True)
    db.add(supplier)
    db.flush()
    product = Product(
        product_name_en="Apple",
        code="APPLE",
        supplier_id=supplier.id,
        unit="KG",
        status=True,
    )
    db.add(product)
    db.flush()
    template = SupplierTemplate(
        supplier_id=supplier.id,
        template_name="Unit template",
        template_file_url="templates/unit.xlsx",
        template_styles={"zones": {"product_data": {}}},
        product_table_config={
            "start_row": 10,
            "columns": {
                "A": "product_code",
                "B": "quantity",
                "C": "unit",
                "D": "unit_price",
            },
        },
    )
    db.add(template)
    db.flush()
    source_row = {
        "line_id": "L1",
        "product_code": "APPLE",
        "product_name": "Apple",
        "quantity": 12,
        "unit": "KG2.2",
    }
    result = {
        **source_row,
        "match_status": "matched",
        "match_score": 1.0,
        "matched_product": {
            "id": product.id,
            "supplier_id": supplier.id,
            "unit": "KG",
        },
        "source_quantity": 12,
        "source_unit": "KG2.2",
        "rfq_quantity": 12,
        "rfq_unit": "KG",
        "conversion_evidence": {
            "verified": verified,
            "rule_id": 20,
            "rule_revision": 1,
            "scope_type": "source_unit",
            "source_unit": "KG2.2",
            "target_unit": "KG",
            "evidence": "verified reusable rule",
        },
    }
    order = Order(
        user_id=1,
        filename="unit.pdf",
        status="matched",
        port_id=1,
        delivery_date="2026-09-23",
        products=[source_row],
        match_results=[result],
    )
    db.add(order)
    db.commit()
    return order, template


def test_verified_order_snapshot_needs_no_legacy_source_file_approval(db):
    """Requiring legacy approval would block every automatically converted row."""

    order, template = setup_inquiry_row(db, verified=True)
    source = SimpleNamespace(source_key="new", version_key="v2", pdf_sha256="sha")

    issues, bindings = prepare_inquiry(db, order, source, unit_approvals=[])

    assert issues == []
    assert bindings == {order.match_results[0]["matched_product"]["supplier_id"]: template.id}
    assert order.match_results[0]["rfq_quantity"] == 12.0
    assert order.match_results[0]["rfq_unit"] == "KG"
    assert order.match_results[0]["conversion_evidence"]["rule_id"] == 20


def test_unverified_order_snapshot_still_requires_legacy_approval(db):
    """Trusting a draft snapshot would bypass the explicit verification gate."""

    order, _template = setup_inquiry_row(db, verified=False)
    source = SimpleNamespace(source_key="new", version_key="v2", pdf_sha256="sha")

    issues, _bindings = prepare_inquiry(db, order, source, unit_approvals=[])

    assert issues == [{"code": "UNIT_CONVERSION_EVIDENCE_REQUIRED", "line_id": "L1"}]
    assert order.match_results[0]["inquiry_exclusion_code"] == "UNIT_CONVERSION_EVIDENCE_REQUIRED"

