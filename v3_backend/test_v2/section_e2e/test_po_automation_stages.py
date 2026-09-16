"""Executable contracts for the eight-stage automatic PO pipeline."""

from domains.document import service as document_service
from domains.document.models import Document
from domains.inquiry import repository as inquiry_repository
from domains.inquiry.models import SupplierTemplate
from domains.inquiry.standard_template import (
    STANDARD_TEMPLATE_ASSET,
    standard_template_definition,
)
from domains.masterdata.models import Country, Port, Product, Supplier
from domains.orders import anomaly
from domains.orders import service as orders_service
from domains.orders.automation import STAGE_NAMES, automatic_from_document
from domains.orders.models import Order
from infrastructure.storage import get_storage


def _seed_scope(db, user):
    country = Country(name="日本", code="JPN")
    db.add(country)
    db.flush()
    port = Port(name="横浜", code="YOK", country_id=country.id)
    supplier = Supplier(name="自动化测试供应商")
    db.add_all([port, supplier])
    db.flush()
    template_definition = standard_template_definition()
    template_url = get_storage().upload(
        "templates",
        "automation-standard-inquiry.xlsx",
        STANDARD_TEMPLATE_ASSET.read_bytes(),
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )
    db.add(
        SupplierTemplate(
            supplier_id=supplier.id,
            supplier_ids=[supplier.id],
            country_id=country.id,
            template_name=template_definition["template_name"],
            template_file_url=template_url,
            field_positions=template_definition["field_positions"],
            has_product_table=True,
            product_table_config=template_definition["product_table_config"],
            template_styles=template_definition["template_styles"],
            created_by=user.id,
        )
    )
    product = Product(
        code="AUTO-APPLE",
        product_name_en="Apple",
        country_id=country.id,
        port_id=port.id,
        supplier_id=supplier.id,
        unit="CA",
        price=12,
        contract_price=18,
        currency="USD",
        status=True,
    )
    db.add(product)
    db.commit()
    return port, product


def _document(db, user, *, po="PO-AUTO", loading_date="2026-10-01", products=None):
    doc = Document(
        user_id=user.id,
        filename=f"{po}.pdf",
        file_type="pdf",
        file_size_bytes=512,
        doc_type="purchase_order",
        status="extracted",
        extraction_method="test-structured",
        extracted_data={
            "metadata": {
                "po_number": po,
                "ship_name": "TEST SHIP",
                "loading_date": loading_date,
                "delivery_date": loading_date,
                "destination_port": "横浜",
                "currency": "USD",
            },
            "products": products
            or [
                {
                    "line_id": "line-1",
                    "source_line": "2",
                    "page": 1,
                    "product_code": "AUTO-APPLE",
                    "product_name": "Apple",
                    "quantity": 2,
                    "unit": "CA",
                    "unit_price": 18,
                }
            ],
        },
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def test_steps_1_to_8_finish_automatically_for_manual_po(db, seed_user):
    _seed_scope(db, seed_user)
    doc = _document(db, seed_user)

    order_id = automatic_from_document(doc.id)

    assert order_id is not None
    db.expire_all()
    order = db.get(Order, order_id)
    assert order.group_id is not None
    trace = order.anomaly_data["pipeline"]
    assert [row["step"] for row in trace] == list(range(1, 9))
    assert [row["name"] for row in trace] == [STAGE_NAMES[i] for i in range(1, 9)]
    assert all(row["status"] not in {"pending", "running"} for row in trace)
    inquiry = inquiry_repository.get_latest_inquiry_by_group(db, order.group_id)
    assert inquiry is not None
    assert inquiry.status == "completed"
    assert order.anomaly_data["schema_version"] == 2


def test_automatic_projection_is_idempotent(db, seed_user):
    _seed_scope(db, seed_user)
    doc = _document(db, seed_user, po="PO-IDEMPOTENT")

    first = automatic_from_document(doc.id)
    second = automatic_from_document(doc.id)

    assert first == second
    assert db.query(Order).filter(Order.document_id == doc.id).count() == 1
    order = db.get(Order, first)
    assert len(inquiry_repository.list_inquiries_by_group(db, order.group_id)) == 1


def test_manual_upload_enters_automatic_pipeline_without_create_click(
    db, seed_user, monkeypatch
):
    _seed_scope(db, seed_user)
    extracted = {
        "markdown": "# Purchase Order\nPO-AUTO-UPLOAD",
        "stats": {"extractor": "test-structured"},
        "metadata": {
            "po_number": "PO-AUTO-UPLOAD",
            "ship_name": "TEST SHIP",
            "loading_date": "2026-10-01",
            "delivery_date": "2026-10-01",
            "destination_port": "横浜",
            "currency": "USD",
        },
        "products": [
            {
                "line_id": "upload-line-1",
                "source_line": "2",
                "page": 1,
                "product_code": "AUTO-APPLE",
                "product_name": "Apple",
                "quantity": 2,
                "unit": "CA",
                "unit_price": 18,
            }
        ],
    }
    monkeypatch.setattr("domains.document.workflow.extract", lambda *_args: extracted)
    monkeypatch.setattr(
        "domains.document.classifier.classify", lambda _data: "purchase_order"
    )

    response = document_service.upload_document(
        db,
        user_id=seed_user.id,
        filename="PO-AUTO-UPLOAD.pdf",
        content=b"%PDF-1.4 test %%EOF",
        content_type="application/pdf",
    )

    db.expire_all()
    order = db.query(Order).filter(Order.document_id == response.id).one()
    assert order.status == "ready"
    assert order.anomaly_data["pipeline"][7]["status"] in {
        "completed",
        "completed_with_warnings",
        "needs_review",
    }
    assert inquiry_repository.get_latest_inquiry_by_group(db, order.group_id) is not None


def test_one_bad_row_is_isolated_while_safe_row_generates(db, seed_user):
    _seed_scope(db, seed_user)
    doc = _document(
        db,
        seed_user,
        po="PO-PARTIAL",
        products=[
            {
                "line_id": "safe",
                "source_line": "2",
                "page": 1,
                "product_code": "AUTO-APPLE",
                "product_name": "Apple",
                "quantity": 2,
                "unit": "CA",
                "unit_price": 18,
            },
            {
                "line_id": "bad",
                "source_line": "3",
                "page": 1,
                "product_code": "AUTO-APPLE",
                "product_name": "Apple",
                "quantity": 0,
                "unit": "CA",
                "unit_price": 18,
            },
        ],
    )

    order_id = automatic_from_document(doc.id)

    db.expire_all()
    order = db.get(Order, order_id)
    inquiry = inquiry_repository.get_latest_inquiry_by_group(db, order.group_id)
    assert inquiry.status == "partial"
    assert inquiry.supplier_count == 1
    assert inquiry.unassigned_count == 1
    finding = next(
        row for row in order.anomaly_data["findings"] if row["code"] == "QUANTITY_INVALID"
    )
    assert finding["source_line"] == "3"
    assert finding["step"] == 5
    assert finding["suggestion"]


def test_missing_loading_date_stops_at_step_6_not_before(db, seed_user):
    _seed_scope(db, seed_user)
    doc = _document(db, seed_user, po="PO-NO-DATE", loading_date=None)

    order_id = automatic_from_document(doc.id)

    db.expire_all()
    order = db.get(Order, order_id)
    trace = {row["step"]: row for row in order.anomaly_data["pipeline"]}
    assert all(trace[step]["status"] == "completed" for step in (1, 2, 3, 4))
    assert trace[5]["status"] in {"completed", "completed_with_anomalies"}
    assert trace[6]["status"] == "needs_review"
    assert trace[7]["status"] == "skipped"
    assert order.group_id is None
    assert any(row["code"] == "LOADING_DATE_REQUIRED" for row in order.anomaly_data["findings"])


def test_anomaly_rules_are_extensible_and_stage_aware(db, seed_user):
    _seed_scope(db, seed_user)
    doc = _document(db, seed_user, po="PO-CUSTOM-RULE")
    order_id = automatic_from_document(doc.id)
    db.expire_all()
    order = db.get(Order, order_id)

    def custom_rule(context):
        return [
            anomaly.finding(
                code="CUSTOM_CUSTOMER_RULE",
                step=8,
                severity="warning",
                scope="order",
                message=f"订单 {context.order.po_number} 命中自定义规则",
                suggestion="按客户策略复核",
            )
        ]

    anomaly.register_rule("CUSTOM_CUSTOMER_RULE", custom_rule)
    try:
        result = anomaly.run_anomaly_check(order)
    finally:
        anomaly.unregister_rule("CUSTOM_CUSTOMER_RULE")

    custom = next(row for row in result["findings"] if row["code"] == "CUSTOM_CUSTOMER_RULE")
    assert custom["step"] == 8
    assert custom["severity"] == "warning"
    assert custom["message"].startswith("订单 PO-CUSTOM-RULE")


def test_manual_anomaly_rerun_preserves_the_eight_stage_trace(db, seed_user):
    _seed_scope(db, seed_user)
    doc = _document(db, seed_user, po="PO-ANOMALY-RERUN")
    order_id = automatic_from_document(doc.id)

    detail = orders_service.run_anomaly_check(
        db,
        order_id=order_id,
        user_id=seed_user.id,
        is_admin=False,
    )

    assert [row["step"] for row in detail.anomaly_data["pipeline"]] == list(range(1, 9))
    assert detail.anomaly_data["pipeline"][7]["status"] not in {"pending", "running"}
