"""Section 5 — inquiry: field-source inspection for the UI panel.

测试目标：
    Pin the contract for `compute_field_sources()` — the helper that powers
    the inquiry tab's "字段映射" panel introduced 2026-05-22.

    Each declared cell in `template.field_positions` must come back with:
      • current resolved value (or None if blank)
      • source tag: metadata / port_master / supplier_master / empty
      • position + sortable cell-order
      • Chinese label (or humanised fallback)

为什么必须有：
    UI consumers downstream depend on the exact `source` enum (4 colored
    badges); silently widening the set or renaming a source value would
    break frontend rendering with no compile-time signal.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from domains.inquiry.field_inspection import (
    FIELD_LABELS_ZH,
    _parse_position,
    compute_field_sources,
)
from domains.inquiry.models import SupplierTemplate
from domains.masterdata.models import Country, Port, Supplier
from domains.orders.models import Order


def _zoned() -> dict:
    return {"zones": {"meta": {}, "product_table": {}}}


def _seed_japan_port(db: Session) -> int:
    c = Country(name="JAPAN", code="JP")
    db.add(c)
    db.flush()
    p = Port(
        name="大阪",
        code="3",
        country_id=c.id,
        location="3-11-8 Chikko, Minato-ku, Osaka City",
        status=True,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p.id


def _seed_supplier_full(db: Session) -> int:
    s = Supplier(
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
    db.add(s)
    db.commit()
    db.refresh(s)
    return s.id


def _seed_supplier_minimal(db: Session) -> int:
    s = Supplier(
        name="㈲田浦",
        phone="046-861-2983",
        email="info@example.com",
        contact="及川浩一",
        address=None,
        fax=None,
        zip_code=None,
        default_payment_method=None,
        default_payment_terms=None,
        status=True,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s.id


def _japan_template(field_positions: dict | None = None) -> SupplierTemplate:
    return SupplierTemplate(
        id=99,
        template_name="日本订单标准",
        field_positions=field_positions
        or {
            "supplier_name": "A3",
            "po_number": "L4",
            "delivery_date": "I7",
            "delivery_address": "I8",
            "ship_name_jp": "I9",
            "supplier_address": "A5",
            "supplier_tel": "A6",
            "supplier_email": "A7",
        },
        product_table_config={"columns": {}, "start_row": 12},
        template_styles=_zoned(),
        has_product_table=True,
    )


def _make_order(
    *,
    port_id: int | None = None,
    metadata: dict | None = None,
    columns: dict | None = None,
) -> Order:
    cols = columns or {}
    return Order(
        user_id=1,
        filename="t.pdf",
        file_url="orders/t.pdf",
        file_type="pdf",
        status="ready",
        port_id=port_id,
        order_metadata=metadata,
        po_number=cols.get("po_number"),
        ship_name=cols.get("ship_name"),
        delivery_date=cols.get("delivery_date"),
        currency=cols.get("currency"),
        destination_port=cols.get("destination_port"),
    )


# ─── Source classification ────────────────────────────────────


def test_metadata_source_when_value_in_order_metadata(session_factory) -> None:
    db = session_factory()
    try:
        order = _make_order(metadata={"po_number": "PO-X", "ship_name_jp": "セレブリティ"})
        fields = compute_field_sources(
            order=order, template=_japan_template(), supplier_id=None, db=db
        )
        by_key = {f.key: f for f in fields}
        assert by_key["po_number"].value == "PO-X"
        assert by_key["po_number"].source == "metadata"
        assert by_key["ship_name_jp"].value == "セレブリティ"
        assert by_key["ship_name_jp"].source == "metadata"
    finally:
        db.close()


def test_metadata_source_when_value_in_order_column(session_factory) -> None:
    """Order columns (po_number/ship_name/...) win over the JSON merge for
    those specific keys. They count as `metadata` source — that's where the
    final value resides, even though it came from a typed DB column."""
    db = session_factory()
    try:
        order = _make_order(columns={"po_number": "PO-COL", "delivery_date": "2026-06-03"})
        fields = compute_field_sources(
            order=order, template=_japan_template(), supplier_id=None, db=db
        )
        by_key = {f.key: f for f in fields}
        assert by_key["po_number"].value == "PO-COL"
        assert by_key["po_number"].source == "metadata"
        assert by_key["delivery_date"].value == "2026-06-03"
        assert by_key["delivery_date"].source == "metadata"
    finally:
        db.close()


def test_port_master_source_for_delivery_address(session_factory) -> None:
    db = session_factory()
    try:
        port_id = _seed_japan_port(db)
        order = _make_order(port_id=port_id)
        fields = compute_field_sources(
            order=order, template=_japan_template(), supplier_id=None, db=db
        )
        by_key = {f.key: f for f in fields}
        assert by_key["delivery_address"].value == "3-11-8 Chikko, Minato-ku, Osaka City"
        assert by_key["delivery_address"].source == "port_master"
    finally:
        db.close()


def test_supplier_master_source_for_supplier_fields(session_factory) -> None:
    db = session_factory()
    try:
        sup_id = _seed_supplier_full(db)
        order = _make_order()
        fields = compute_field_sources(
            order=order, template=_japan_template(), supplier_id=sup_id, db=db
        )
        by_key = {f.key: f for f in fields}
        assert by_key["supplier_address"].value == "東京都港区芝公園4-2-8"
        assert by_key["supplier_address"].source == "supplier_master"
        assert by_key["supplier_tel"].value == "03-5492-3105"
        assert by_key["supplier_tel"].source == "supplier_master"
        assert by_key["supplier_email"].value == "cruise.merit@example.com"
        assert by_key["supplier_email"].source == "supplier_master"
        # supplier_name maps to template "supplier_name" key (A3).
        assert by_key["supplier_name"].value == "株式会社　松武"
        assert by_key["supplier_name"].source == "supplier_master"
    finally:
        db.close()


def test_empty_source_when_no_data_anywhere(session_factory) -> None:
    db = session_factory()
    try:
        sup_id = _seed_supplier_minimal(db)  # nulls for fax/zip/address
        order = _make_order()  # no port_id, no metadata
        fields = compute_field_sources(
            order=order, template=_japan_template(), supplier_id=sup_id, db=db
        )
        by_key = {f.key: f for f in fields}
        # No port → no port lookup → empty
        assert by_key["delivery_address"].value is None
        assert by_key["delivery_address"].source == "empty"
        # Supplier has these as None → must report empty, NOT inject ""
        assert by_key["supplier_address"].value is None
        assert by_key["supplier_address"].source == "empty"
        # No metadata for ship_name_jp + no master mapping → empty
        assert by_key["ship_name_jp"].value is None
        assert by_key["ship_name_jp"].source == "empty"
    finally:
        db.close()


def test_metadata_wins_over_port_master(session_factory) -> None:
    """If the user has already typed a delivery_address (stored in
    order.order_metadata), the Port master must NOT shadow it."""
    db = session_factory()
    try:
        port_id = _seed_japan_port(db)
        order = _make_order(
            port_id=port_id, metadata={"delivery_address": "用户手填地址"}
        )
        fields = compute_field_sources(
            order=order, template=_japan_template(), supplier_id=None, db=db
        )
        by_key = {f.key: f for f in fields}
        assert by_key["delivery_address"].value == "用户手填地址"
        assert by_key["delivery_address"].source == "metadata"
    finally:
        db.close()


# ─── Output shape ─────────────────────────────────────────────


def test_fields_sorted_by_cell_position(session_factory) -> None:
    """UI displays fields in cell visual order — A3 before A5 before I7
    before I8. Without stable sort, every reload reshuffles the panel."""
    db = session_factory()
    try:
        order = _make_order()
        tpl = _japan_template(
            {"po_number": "L4", "supplier_name": "A3", "supplier_address": "A5",
             "delivery_address": "I8", "delivery_date": "I7"}
        )
        fields = compute_field_sources(
            order=order, template=tpl, supplier_id=None, db=db
        )
        positions = [f.position for f in fields]
        # Row order: A3 (r3) → A5 (r5) → L4 (r4) — wait r4 < r5, so order should be A3, L4, A5, I7, I8
        # Actually it's by (row, col): r3,c1 / r4,c12 / r5,c1 / r7,c9 / r8,c9
        assert positions == ["A3", "L4", "A5", "I7", "I8"]
    finally:
        db.close()


def test_field_label_lookup(session_factory) -> None:
    """Known keys get their Chinese label; unknown keys get a humanised fallback."""
    db = session_factory()
    try:
        order = _make_order()
        tpl = _japan_template({"delivery_address": "I8", "custom_made_up_key": "Z9"})
        fields = compute_field_sources(
            order=order, template=tpl, supplier_id=None, db=db
        )
        by_key = {f.key: f for f in fields}
        assert by_key["delivery_address"].label == FIELD_LABELS_ZH["delivery_address"]
        # Unknown key falls back to humanised form.
        assert by_key["custom_made_up_key"].label == "Custom Made Up Key"
    finally:
        db.close()


def test_parse_position_handles_multiletter_columns() -> None:
    """AA1 must sort after Z1. Without this, fields with double-letter
    columns (AA, AB, ..., AZ, BA, ...) would land in the wrong slot."""
    assert _parse_position("Z1") < _parse_position("AA1")
    assert _parse_position("A1") < _parse_position("A2")
    assert _parse_position("invalid") == (10_000, 10_000)


def test_template_with_no_field_positions_yields_empty(session_factory) -> None:
    """Korean / Jeju templates have field_positions={} — the inspector must
    return an empty list (not crash, not synthesize fake fields). Frontend
    relies on this to show "此模板未声明字段位置" instead of an empty grid
    that looks broken."""
    db = session_factory()
    try:
        tpl = SupplierTemplate(
            id=88,
            template_name="韩国模版",
            field_positions={},
            product_table_config={"columns": {}, "start_row": 12},
            template_styles=_zoned(),
            has_product_table=True,
        )
        order = _make_order()
        fields = compute_field_sources(
            order=order, template=tpl, supplier_id=None, db=db
        )
        assert fields == []
    finally:
        db.close()


def test_different_templates_produce_different_field_sets(session_factory) -> None:
    """Switching from template 14 (16 fields) to template 13 (17 fields with
    different keys like voyage/destination/ship_name_alt) must produce a
    materially different field set — guards against the bug where the panel
    showed stale fields after template switch."""
    db = session_factory()
    try:
        order = _make_order()
        tpl_14 = _japan_template(
            {"po_number": "L4", "delivery_address": "I8", "supplier_tel": "A6"}
        )
        tpl_13 = SupplierTemplate(
            id=13,
            template_name="日本模版测试2",
            field_positions={
                "po_number": "L4",
                "voyage": "K6",
                "destination": "H10",
                "ship_name_alt": "I12",
                "supplier_email": "A8",
            },
            product_table_config={"columns": {}, "start_row": 12},
            template_styles=_zoned(),
            has_product_table=True,
        )
        keys_14 = {
            f.key
            for f in compute_field_sources(
                order=order, template=tpl_14, supplier_id=None, db=db
            )
        }
        keys_13 = {
            f.key
            for f in compute_field_sources(
                order=order, template=tpl_13, supplier_id=None, db=db
            )
        }
        # Template-specific fields must show only in their template's response.
        assert "delivery_address" in keys_14
        assert "delivery_address" not in keys_13
        assert "voyage" in keys_13
        assert "voyage" not in keys_14
        assert "ship_name_alt" in keys_13
        assert "ship_name_alt" not in keys_14
    finally:
        db.close()
