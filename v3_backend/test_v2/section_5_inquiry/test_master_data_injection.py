"""Section 5 — inquiry: master-data injection into template metadata.

测试目标：
    Pin the precedence chain in `_order_metadata()` (2026-05-22):
      1. order.order_metadata JSON     (highest — LLM/user)
      2. order column overrides
      3. Port.location → delivery_address
      4. Supplier.address/phone/... → supplier_*

    Prod bug 2026-05-22: order 106 (Osaka cruise) showed Tokyo Harumi address
    in inquiry Excel because the supplier-uploaded template had stale sample
    data in I8 and our render code skipped writing when metadata had no
    `delivery_address`. The fix: pull from Port master + Supplier master at
    render-prep time so the cell always gets a real value (or stays blank,
    not stale).

为什么必须有：
    Without this test, future changes to `_order_metadata()` (e.g. adding a
    layer, changing precedence) silently break the customer-visible address
    output. The precedence chain is invisible to anyone reading the renderer
    in isolation.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from domains.inquiry.orchestrator import _order_metadata
from domains.masterdata.models import Country, Port, Supplier
from domains.orders.models import Order


@pytest.fixture
def osaka_port(session_factory) -> int:
    """Seed Japan + Osaka port. Returns port id."""
    db: Session = session_factory()
    try:
        country = Country(name="JAPAN", code="JP")
        db.add(country)
        db.flush()
        port = Port(
            name="大阪",
            code="3",
            country_id=country.id,
            location="3-11-8 Chikko, Minato-ku, Osaka City",
            status=True,
        )
        db.add(port)
        db.commit()
        db.refresh(port)
        return port.id
    finally:
        db.close()


@pytest.fixture
def matsutake_supplier(session_factory) -> int:
    """Seed a supplier with all contact fields populated. Returns supplier id."""
    db: Session = session_factory()
    try:
        supplier = Supplier(
            name="株式会社　松武",
            contact="阿明",
            email="cruise.merit@example.com",
            phone="03-5492-3105",
            address="東京都港区芝公園4-2-8",
            zip_code="105-0011",
            fax="03-5492-3106",
            default_payment_method="月末締め翌月末払い",
            default_payment_terms="30日",
            status=True,
        )
        db.add(supplier)
        db.commit()
        db.refresh(supplier)
        return supplier.id
    finally:
        db.close()


def _make_order(
    *,
    user_id: int = 1,
    port_id: int | None = None,
    metadata: dict | None = None,
    columns: dict | None = None,
) -> Order:
    cols = columns or {}
    return Order(
        user_id=user_id,
        filename="t.pdf",
        file_url="orders/t.pdf",
        file_type="pdf",
        status="ready",
        port_id=port_id,
        order_metadata=metadata,
        po_number=cols.get("po_number"),
        ship_name=cols.get("ship_name"),
        vendor_name=cols.get("vendor_name"),
        order_date=cols.get("order_date"),
        delivery_date=cols.get("delivery_date"),
        currency=cols.get("currency"),
        destination_port=cols.get("destination_port"),
    )


# ─── Layer 3: Port lookup ─────────────────────────────────────


def test_port_lookup_injects_delivery_address_when_missing(
    session_factory, osaka_port
) -> None:
    """port_id set + no delivery_address in metadata → Port.location injected."""
    db = session_factory()
    try:
        order = _make_order(port_id=osaka_port, metadata={"po_number": "P1"})
        result = _order_metadata(order, db=db, supplier_id=None)
        assert result["delivery_address"] == "3-11-8 Chikko, Minato-ku, Osaka City"
    finally:
        db.close()


def test_port_lookup_skipped_when_no_db(session_factory, osaka_port) -> None:
    """db=None → master-data layer entirely skipped (legacy callers stay clean)."""
    order = _make_order(port_id=osaka_port)
    result = _order_metadata(order)
    assert "delivery_address" not in result


def test_port_lookup_skipped_when_port_id_none(session_factory) -> None:
    """port_id=None → no port lookup attempted, no key added."""
    db = session_factory()
    try:
        order = _make_order(port_id=None, metadata={"po_number": "P1"})
        result = _order_metadata(order, db=db, supplier_id=None)
        assert "delivery_address" not in result
    finally:
        db.close()


# ─── Layer 4: Supplier lookup ─────────────────────────────────


def test_supplier_lookup_injects_all_contact_fields(
    session_factory, matsutake_supplier
) -> None:
    """supplier_id provided → all populated Supplier columns map into metadata."""
    db = session_factory()
    try:
        order = _make_order()
        result = _order_metadata(order, db=db, supplier_id=matsutake_supplier)
        assert result["supplier_name"] == "株式会社　松武"
        assert result["supplier_contact"] == "阿明"
        assert result["supplier_email"] == "cruise.merit@example.com"
        assert result["supplier_tel"] == "03-5492-3105"
        assert result["supplier_address"] == "東京都港区芝公園4-2-8"
        assert result["supplier_zip_code"] == "105-0011"
        assert result["supplier_fax"] == "03-5492-3106"
        assert result["payment_method"] == "月末締め翌月末払い"
        assert result["payment_date"] == "30日"
    finally:
        db.close()


def test_supplier_lookup_skips_null_master_fields(session_factory) -> None:
    """Supplier rows often have only phone/email populated. Null/empty fields
    must NOT be injected — that would write empty strings over template
    placeholder values (defeating the whole render-flush fix)."""
    db = session_factory()
    try:
        partial = Supplier(
            name="㈲田浦中央食品",
            contact="及川浩一",
            email="info@example.com",
            phone="046-861-2983",
            address=None,
            fax=None,
            zip_code=None,
            default_payment_method=None,
            default_payment_terms=None,
            status=True,
        )
        db.add(partial)
        db.commit()
        db.refresh(partial)

        order = _make_order()
        result = _order_metadata(order, db=db, supplier_id=partial.id)
        assert result["supplier_contact"] == "及川浩一"
        assert result["supplier_email"] == "info@example.com"
        assert result["supplier_tel"] == "046-861-2983"
        # Null fields must not appear at all (renderer treats absent == blank cell).
        assert "supplier_address" not in result
        assert "supplier_fax" not in result
        assert "supplier_zip_code" not in result
        assert "payment_method" not in result
    finally:
        db.close()


# ─── Precedence: user / LLM values win over master ────────────


def test_user_metadata_overrides_port_master(session_factory, osaka_port) -> None:
    """If order_metadata already has delivery_address (LLM-extracted or
    user-edited), it must NOT be overwritten by the Port lookup."""
    db = session_factory()
    try:
        order = _make_order(
            port_id=osaka_port,
            metadata={"delivery_address": "Custom 直接指定 address"},
        )
        result = _order_metadata(order, db=db, supplier_id=None)
        assert result["delivery_address"] == "Custom 直接指定 address"
    finally:
        db.close()


def test_supplier_override_beats_supplier_master_legacy_test(
    session_factory, matsutake_supplier
) -> None:
    """Per-supplier user override must beat supplier-master default.

    Updated 2026-05-22: previously top-level order_metadata.supplier_email
    would win, but that scheme caused cross-supplier contamination so
    supplier-level keys now MUST live under supplier_overrides[supplier_id].
    See `test_supplier_override_isolation.py` for the full contract."""
    db = session_factory()
    try:
        order = _make_order(
            metadata={
                "supplier_overrides": {
                    str(matsutake_supplier): {"supplier_email": "special-buyer@ship.com"}
                }
            }
        )
        result = _order_metadata(order, db=db, supplier_id=matsutake_supplier)
        assert result["supplier_email"] == "special-buyer@ship.com"
        # Other supplier fields still come from master (precedence is per-key).
        assert result["supplier_tel"] == "03-5492-3105"
    finally:
        db.close()
