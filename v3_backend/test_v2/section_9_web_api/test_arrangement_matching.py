from datetime import datetime

import pytest

from domains.masterdata.models import Country, Port, Product, Supplier
from domains.orders.groups.matching import ArrangementMatchError, match_arrangement
from domains.orders.models import Order, OrderGroup
from test_v2.fixtures.helpers import seed_user


def _setup_group(db):
    user = seed_user(db, email="arrangement-match@test")
    country = Country(name="Japan", code="JPN")
    db.add(country)
    db.flush()
    port = Port(name="東京", code="TYO", country_id=country.id)
    supplier = Supplier(name="Supplier")
    db.add_all([port, supplier])
    db.flush()
    group = OrderGroup(
        user_id=user.id,
        name="2026-09-20 · 東京",
        loading_date="2026-09-20",
    )
    db.add(group)
    db.flush()
    return user, country, port, supplier, group


def _order(db, *, user_id, group_id, port_id, po, code, line_id="line-00001"):
    row = Order(
        user_id=user_id,
        group_id=group_id,
        port_id=port_id,
        filename=f"{po}.pdf",
        po_number=po,
        loading_date="2026-09-20",
        delivery_date="2026-09-18",
        products=[
            {
                "line_id": line_id,
                "source_line": "1",
                "page": 1,
                "product_code": code,
                "product_name": "Product",
                "quantity": "2",
                "unit": "CA",
            }
        ],
        status="ready",
    )
    db.add(row)
    db.flush()
    return row


def test_matches_all_orders_without_merging_equal_skus(db):
    user, country, port, supplier, group = _setup_group(db)
    product = Product(
        code="SAME",
        product_name_en="Same product",
        country_id=country.id,
        port_id=port.id,
        supplier_id=supplier.id,
        unit="CA",
        price=100,
        status=True,
    )
    db.add(product)
    first = _order(
        db, user_id=user.id, group_id=group.id, port_id=port.id, po="PO-A", code="SAME"
    )
    second = _order(
        db, user_id=user.id, group_id=group.id, port_id=port.id, po="PO-B", code="SAME"
    )
    db.commit()

    result = match_arrangement(db, group.id)

    assert result["status"] == "completed"
    assert result["total"] == result["matched"] == 2
    assert [item["source_po_number"] for item in result["items"]] == ["PO-A", "PO-B"]
    assert len({item["arrangement_line_id"] for item in result["items"]}) == 2
    assert len(first.match_results) == len(second.match_results) == 1


def test_uses_loading_day_for_product_effective_window(db):
    user, country, port, supplier, group = _setup_group(db)
    db.add(
        Product(
            code="FUTURE",
            product_name_en="Future product",
            country_id=country.id,
            port_id=port.id,
            supplier_id=supplier.id,
            unit="CA",
            price=100,
            status=True,
            effective_from=datetime(2026, 9, 19),
        )
    )
    _order(
        db,
        user_id=user.id,
        group_id=group.id,
        port_id=port.id,
        po="PO-A",
        code="FUTURE",
    )
    db.commit()

    result = match_arrangement(db, group.id)

    assert result["status"] == "completed"
    assert result["items"][0]["matched_product"]["code"] == "FUTURE"


def test_preserves_unmatched_rows_with_source_and_reason(db):
    user, _country, port, _supplier, group = _setup_group(db)
    _order(
        db,
        user_id=user.id,
        group_id=group.id,
        port_id=port.id,
        po="PO-MISSING",
        code="NOT-IN-DB",
    )
    db.commit()

    result = match_arrangement(db, group.id)

    assert result["status"] == "unmatched"
    assert result["items"][0]["source_po_number"] == "PO-MISSING"
    assert result["items"][0]["match_reason"] == "未找到匹配商品"


def test_reuses_verified_conversion_only_when_exact_match_is_unchanged(db):
    user, country, port, supplier, group = _setup_group(db)
    product = Product(
        code="CASE",
        product_name_en="Case product",
        country_id=country.id,
        port_id=port.id,
        supplier_id=supplier.id,
        unit="CA",
        price=100,
        status=True,
    )
    db.add(product)
    row = _order(
        db,
        user_id=user.id,
        group_id=group.id,
        port_id=port.id,
        po="PO-CONVERTED",
        code="CASE",
    )
    db.flush()
    row.match_results = [
        {
            **row.products[0],
            "arrangement_line_id": f"order-{row.id}:line-00001",
            "match_status": "matched",
            "matched_product": {"id": product.id},
            "rfq_quantity": 4.0,
            "rfq_unit": "CA",
            "source_quantity": "8",
            "source_unit": "EA",
            "conversion_evidence": {"evidence": "2 EA per CA"},
        }
    ]
    db.commit()

    result = match_arrangement(db, group.id)

    item = result["items"][0]
    assert item["rfq_quantity"] == 4.0
    assert item["source_unit"] == "EA"
    assert item["conversion_evidence"]["evidence"] == "2 EA per CA"


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("loading_date", None, "缺少或无法识别装船日"),
        ("port_id", None, "未选择目标港口"),
    ],
)
def test_refuses_arrangement_without_deterministic_scope(db, field, value, message):
    user, _country, port, _supplier, group = _setup_group(db)
    row = _order(
        db, user_id=user.id, group_id=group.id, port_id=port.id, po="PO-A", code="ANY"
    )
    setattr(row, field, value)
    db.commit()

    with pytest.raises(ArrangementMatchError, match=message):
        match_arrangement(db, group.id)
