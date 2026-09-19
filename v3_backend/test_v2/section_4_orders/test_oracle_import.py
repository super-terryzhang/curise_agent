"""Acceptance boundaries for the single-PO pipeline; no production or model I/O."""

from copy import deepcopy

import pytest

from apps.jobs.oracle_po import import_po
from domains.document.models import Document
from domains.inquiry.models import Inquiry, SupplierTemplate
from domains.inquiry.repository import list_inquiries_by_group
from domains.masterdata.models import Country, Port, Product, Supplier
from domains.orders.models import Order
from domains.orders.oracle_models import OraclePOImport


@pytest.fixture
def setup_po(db, seed_user, monkeypatch):
    record = {
        "POHeaderId": 100,
        "OrderNumber": "PO-ACCEPTANCE",
        "Revision": 0,
        "StatusCode": "OPEN",
        "CreationDate": "2026-09-01T00:00:00+00:00",
        "LastUpdateDate": "2026-09-01T00:00:00+00:00",
    }
    parsed = {
        "document": {
            "po_number": record["OrderNumber"],
            "metadata": {
                "ship_name": "Test Ship",
                "destination_name": "TOKYO",
                "delivery_date": "2026-09-11",
                "loading_date": "2026-09-11",
            },
            "lines": [
                {
                    "line_id": "line-00001",
                    "source_line": "1",
                    "page": 1,
                    "product_code": "P1",
                    "description": "Test product",
                    "quantity": "2",
                    "unit": "CA",
                }
            ],
        },
        "quality": {
            "identity_verified": True,
            "source_row_check": {
                "verified": True,
                "rows": [
                    {"page": 1, "source_line": "1", "evidence": ["2.00", "CA", "125.00", "250.00"]}
                ],
            },
            "issues": [],
        },
        "provenance": {"raw_model_result": {"complete": True}},
    }
    db.add(Country(id=9, name="Japan", code="JPN"))
    db.add(Port(id=28, name="東京", code="10", country_id=9))
    db.add(Supplier(id=45, name="Test Supplier"))
    db.add(
        Product(
            id=1759,
            code="P1",
            product_name_en="Test product",
            country_id=9,
            port_id=28,
            supplier_id=45,
            unit="CA",
            price=10,
            currency="JPY",
            status=True,
        )
    )
    db.add(
        SupplierTemplate(
            id=14,
            template_name="Test template",
            supplier_ids=[45],
            country_id=9,
            template_file_url="fixture.xlsx",
            field_positions={"po_number": "A1"},
            template_styles={"zones": {}},
            product_table_config={
                "start_row": 7,
                "columns": {"A": "product_code", "B": "quantity", "C": "unit", "D": "unit_price"},
            },
        )
    )
    db.commit()
    monkeypatch.setattr("apps.jobs.oracle_po.structure", lambda *a, **k: deepcopy(parsed))
    monkeypatch.setattr(
        "apps.jobs.oracle_po.read_pdf",
        lambda p: {"pages": [{"text": "Requested Delivery Date: 2026-09-11"}]},
    )
    from io import BytesIO

    from openpyxl import Workbook

    buffer = BytesIO()
    Workbook().save(buffer)
    monkeypatch.setattr(
        "domains.inquiry._supplier_worker._read_template_bytes", lambda t: buffer.getvalue()
    )

    class Client:
        downloads = 0

        def download(self, _):
            self.downloads += 1
            return b"%PDF-1.4\nfixture\n%%EOF"

        def list_orders(self, *, record_issues=None):
            return [record]

    return record, parsed, Client(), seed_user


def run(setup_po, tmp_path, **kwargs):
    record, _, client, user = setup_po
    return import_po(record, client=client, user_id=user.id, cache_dir=tmp_path, **kwargs)


def test_complete_and_same_version_reused(db, setup_po, tmp_path):
    first = run(setup_po, tmp_path)
    second = run(setup_po, tmp_path)
    assert first["status"] == "completed", first
    assert second == first
    assert setup_po[2].downloads == 1
    assert db.query(Document).count() == db.query(Order).count() == db.query(Inquiry).count() == 1
    order = db.get(Order, first["order_id"])
    assert order.products[0]["quantity"] == "2"
    assert order.match_results[0]["rfq_quantity"] == 2
    assert order.group_id is not None


def test_same_code_source_rows_survive(db, setup_po, tmp_path):
    parsed = setup_po[1]
    row = deepcopy(parsed["document"]["lines"][0])
    row.update(line_id="line-00002", source_line="2", quantity="3")
    parsed["document"]["lines"].append(row)
    parsed["quality"]["source_row_check"]["rows"].append(
        {"page": 1, "source_line": "2", "evidence": ["3.00", "CA", "125.00", "375.00"]}
    )
    result = run(setup_po, tmp_path)
    assert result["status"] == "completed", result
    order = db.get(Order, result["order_id"])
    assert [p["quantity"] for p in order.products] == ["2", "3"]
    assert len(order.match_results) == 2


def test_new_po_in_same_arrangement_creates_cumulative_version(db, setup_po, tmp_path):
    first = run(setup_po, tmp_path)
    second_record = {
        **setup_po[0],
        "POHeaderId": 101,
        "OrderNumber": "PO-ACCEPTANCE-2",
    }
    setup_po[2].list_orders = lambda **_kwargs: [setup_po[0], second_record]

    second = import_po(
        second_record,
        client=setup_po[2],
        user_id=setup_po[3].id,
        cache_dir=tmp_path,
    )

    assert first["status"] == second["status"] == "completed"
    first_order = db.get(Order, first["order_id"])
    second_order = db.get(Order, second["order_id"])
    assert first_order.group_id == second_order.group_id
    versions = list_inquiries_by_group(db, first_order.group_id)
    assert [row.version for row in versions] == [2, 1]
    assert [len(row.member_snapshot) for row in versions] == [2, 1]
    assert {item["source_po_number"] for item in versions[0].match_snapshot} == {
        "PO-ACCEPTANCE",
        "PO-ACCEPTANCE-2",
    }


@pytest.mark.parametrize(
    "change,code,inquiry_created",
    [
        ("coverage", "SOURCE_ROWS_UNVERIFIED", True),
        ("identity", "SOURCE_IDENTITY_UNVERIFIED", False),
        ("quantity", "SOURCE_AMOUNT_MISMATCH", True),
        ("unit", "UNIT_CONVERSION_EVIDENCE_REQUIRED", True),
        ("port", "DESTINATION_REQUIRES_REVIEW", False),
        ("date", "SOURCE_DELIVERY_DATE_UNVERIFIED", False),
    ],
)
def test_anomalies_stop_only_the_unsafe_scope(
    db, setup_po, tmp_path, change, code, inquiry_created
):
    parsed = setup_po[1]
    if change == "coverage":
        parsed["quality"]["source_row_check"]["verified"] = False
    if change == "identity":
        parsed["quality"]["identity_verified"] = False
    if change == "quantity":
        parsed["document"]["lines"][0]["quantity"] = "NaN"
    if change == "unit":
        parsed["document"]["lines"][0]["unit"] = "KG"
    if change == "port":
        parsed["document"]["metadata"]["destination_name"] = "unknown"
    if change == "date":
        parsed["document"]["metadata"]["delivery_date"] = None
    result = run(setup_po, tmp_path)
    assert result["status"] == "needs_review", result
    assert code in [i["code"] for i in result["issues"]]
    assert bool(db.query(Inquiry).count()) is inquiry_created
    if result["order_id"]:
        order = db.get(Order, result["order_id"])
        assert len(order.anomaly_data["pipeline"]) == 8


def test_multiple_exact_candidates_block(db, setup_po, tmp_path):
    db.add(
        Product(
            id=1760,
            code="P1",
            product_name_en="Conflict",
            country_id=9,
            port_id=28,
            supplier_id=45,
            unit="CA",
            price=10,
            currency="JPY",
            status=True,
        )
    )
    db.commit()
    result = run(setup_po, tmp_path)
    assert result["status"] == "needs_review", result
    assert "EXACT_UNIQUE_MATCH_REQUIRED" in [row["code"] for row in result["issues"]]
    assert db.query(Inquiry).count() == 1


def test_unbound_template_is_not_automatically_selected(db, setup_po, tmp_path):
    db.get(SupplierTemplate, 14).supplier_ids = []
    db.commit()
    result = run(setup_po, tmp_path)
    assert "TEMPLATE_BINDING_REQUIRED" in [row["code"] for row in result["issues"]], result
    inquiry = db.query(Inquiry).one()
    assert inquiry.supplier_count == 0
    assert inquiry.unassigned_count == 1


def test_historical_order_not_duplicated(db, setup_po, tmp_path):
    db.add(
        Order(
            user_id=setup_po[3].id,
            filename="old.pdf",
            po_number=None,
            order_metadata={"po_number": "PO-ACCEPTANCE"},
        )
    )
    db.commit()
    result = run(setup_po, tmp_path)
    assert result["issues"][0]["code"] == "EXISTING_PO_REQUIRES_LINK"
    assert setup_po[2].downloads == 0
    assert db.query(Order).count() == 1


def test_source_changed_stops_generation(db, setup_po, tmp_path):
    record = deepcopy(setup_po[0])
    record["Revision"] = 1
    setup_po[2].list_orders = lambda **_kwargs: [record]
    result = run(setup_po, tmp_path)
    assert result["issues"][0]["code"] == "SOURCE_CHANGED_DURING_IMPORT", result
    assert db.query(Inquiry).count() == 0


def test_unrelated_invalid_oracle_row_does_not_block_source_recheck(db, setup_po, tmp_path):
    from infrastructure.oracle.adapter import IntegrationError

    def list_orders(*, record_issues=None):
        if record_issues is None:
            raise IntegrationError("ORACLE_INVALID_IDENTITY")
        record_issues.append({
            "po_number": "PO-UNRELATED", "oracle_status": "PENDING ACKNOWLEDGMENT",
            "code": "ORACLE_INVALID_IDENTITY", "field": "Revision",
        })
        return [setup_po[0]]

    setup_po[2].list_orders = list_orders
    result = run(setup_po, tmp_path)
    assert result["status"] == "completed", result
    assert db.query(Inquiry).count() == 1


def test_download_error_is_recorded(db, setup_po, tmp_path):
    from infrastructure.oracle.adapter import IntegrationError

    def fail(_):
        raise IntegrationError("ORACLE_NETWORK_ERROR")

    setup_po[2].download = fail
    result = run(setup_po, tmp_path)
    assert result["status"] == "failed"
    assert result["error_code"] == "ORACLE_NETWORK_ERROR"
    assert db.query(OraclePOImport).one().stage == "download"


@pytest.mark.parametrize(
    "bad_scope", [None, "port_id", "source_quantity", "pdf_sha256", "supplier_quantity"]
)
def test_conversion_is_exactly_scoped(db, setup_po, tmp_path, bad_scope):
    import hashlib

    from infrastructure.oracle.adapter import identity

    setup_po[1]["document"]["lines"][0]["unit"] = "CA10.0"
    proof = {
        **identity(setup_po[0]),
        "pdf_sha256": hashlib.sha256(b"%PDF-1.4\nfixture\n%%EOF").hexdigest(),
        "line_id": "line-00001",
        "product_id": 1759,
        "supplier_id": 45,
        "port_id": 28,
        "delivery_date": "2026-09-11",
        "source_unit": "CA10.0",
        "supplier_unit": "CA",
        "pack_size": None,
        "source_quantity": "2",
        "supplier_quantity": "4",
        "base_unit": "EA",
        "base_per_supplier_unit": "5",
        "verified": True,
        "evidence": "Synthetic case: source contains 10 each, supplier case contains 5 each",
    }
    if bad_scope:
        proof[bad_scope] = 999 if bad_scope == "port_id" else "9"
    result = run(setup_po, tmp_path, unit_approvals=[proof])
    if bad_scope:
        assert result["status"] == "needs_review", result
        inquiry = db.query(Inquiry).one()
        assert inquiry.supplier_count == 0
        assert inquiry.unassigned_count == 1
    else:
        assert result["status"] == "completed", result
        order = db.get(Order, result["order_id"])
        assert order.products[0]["quantity"] == "2"
        assert order.products[0]["unit"] == "CA10.0"
        assert order.match_results[0]["rfq_quantity"] == 4
        from domains.inquiry.models import InquirySupplier

        assert db.query(InquirySupplier).one().subtotal == 40


def test_template_download_failure_never_falls_back(db, setup_po, tmp_path, monkeypatch):
    monkeypatch.setattr("domains.inquiry._supplier_worker._read_template_bytes", lambda t: None)
    result = run(setup_po, tmp_path)
    assert result["status"] == "needs_review", result
    assert "SUPPLIER_FILE_FAILED" in [row["code"] for row in result["issues"]]
    from domains.inquiry.models import InquirySupplier

    row = db.query(InquirySupplier).one()
    assert row.status == "error" and row.excel_file_url is None


def test_import_is_visible_via_existing_api(db, setup_po, tmp_path, client):
    result = run(setup_po, tmp_path)
    assert result["status"] == "completed", result
    login = client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "password123"}
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    for route in [
        f"/api/documents/{result['document_id']}",
        f"/api/orders/{result['order_id']}",
        f"/api/orders/{result['order_id']}/inquiry-readiness",
    ]:
        response = client.get(route, headers=headers)
        assert response.status_code == 200, response.text


def test_source_price_is_kept_separate_from_supplier_price(db, setup_po, tmp_path):
    setup_po[1]["quality"]["source_row_check"]["rows"] = [
        {"page": 1, "source_line": "1", "evidence": ["2.00", "CA", "125.00", "250.00"]}
    ]
    result = run(setup_po, tmp_path)
    assert result["status"] == "completed", result
    order = db.get(Order, result["order_id"])
    assert order.products[0]["unit_price"] == 125
    assert order.total_amount == 250
    assert order.match_results[0]["matched_product"]["price"] == 10


def test_document_override_returns_without_losing_caller(db, setup_po, tmp_path):
    from domains.document.service import apply_metadata_overrides

    result = run(setup_po, tmp_path)
    response = apply_metadata_overrides(
        db,
        document_id=result["document_id"],
        user_id=setup_po[3].id,
        is_admin=True,
        fields={"loading_date": "2026-09-11"},
    )
    assert response.document_id == result["document_id"]
    assert (
        db.get(Document, result["document_id"]).extracted_data["manual_overrides"]["loading_date"]
        == "2026-09-11"
    )
