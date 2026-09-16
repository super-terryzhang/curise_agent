"""Multiple non-overlapping price intervals selected by loading day."""

from datetime import date

import pytest

from domains.masterdata.errors import Conflict
from domains.masterdata.models import Country, Port, Product, ProductPricePeriod, Supplier
from domains.masterdata.price_periods import attach_effective_prices, create_period
from domains.masterdata.schemas import ProductPricePeriodCreate
from domains.orders.groups.matching import match_arrangement
from domains.orders.models import Order, OrderGroup
from test_v2.fixtures.helpers import login, make_excel, seed_user


def _product(db):
    country = Country(name="Japan", code="JPN")
    db.add(country)
    db.flush()
    port = Port(name="Tokyo", code="TYO", country_id=country.id)
    supplier = Supplier(name="Supplier")
    db.add_all([port, supplier])
    db.flush()
    product = Product(
        product_name_en="Period Product",
        code="PERIOD",
        country_id=country.id,
        port_id=port.id,
        supplier_id=supplier.id,
        price=99,
        contract_price=199,
        currency="JPY",
        unit="CA",
        status=True,
    )
    db.add(product)
    db.commit()
    return product, country, port


def _create(db, product, price_type, amount, start, end):
    return create_period(
        db,
        product.id,
        ProductPricePeriodCreate(
            price_type=price_type,
            amount=amount,
            effective_from=date.fromisoformat(start),
            effective_to=date.fromisoformat(end),
        ),
        actor_id=None,
    )


def test_multiple_periods_are_allowed_but_overlap_is_rejected(db):
    product, _country, _port = _product(db)
    _create(db, product, "purchase", 10, "2026-01-01", "2026-06-30")
    _create(db, product, "purchase", 20, "2026-07-01", "2026-12-31")

    with pytest.raises(Conflict, match="重叠"):
        _create(db, product, "purchase", 15, "2026-06-15", "2026-07-15")

    assert db.query(ProductPricePeriod).count() == 2


def test_legacy_price_is_usable_with_explicit_warning(db):
    product, _country, _port = _product(db)

    attach_effective_prices(db, [product], date(2026, 8, 1))

    assert product._effective_prices["purchase"]["amount"] == 99
    assert product._effective_prices["selling"]["amount"] == 199
    assert product._effective_prices["purchase"]["warning"] == "采购价期间未配置"
    assert product._effective_prices["selling"]["warning"] == "卖价期间未配置"


def test_configured_gap_does_not_fall_back_to_legacy_price(db):
    product, _country, _port = _product(db)
    _create(db, product, "purchase", 10, "2026-01-01", "2026-01-31")

    attach_effective_prices(db, [product], date(2026, 8, 1))

    selected = product._effective_prices["purchase"]
    assert selected["amount"] is None
    assert selected["source"] == "period"
    assert "未命中采购价期间" in selected["warning"]


def test_arrangement_matching_selects_both_prices_by_loading_day(db):
    product, _country, port = _product(db)
    purchase = _create(db, product, "purchase", 20, "2026-07-01", "2026-12-31")
    selling = _create(db, product, "selling", 40, "2026-07-01", "2026-12-31")
    user = seed_user(db, email="price-day@test")
    group = OrderGroup(
        user_id=user.id,
        name="2026-07-15 · Tokyo",
        loading_date="2026-07-15",
    )
    db.add(group)
    db.flush()
    db.add(
        Order(
            user_id=user.id,
            group_id=group.id,
            port_id=port.id,
            filename="po.pdf",
            po_number="PO-PRICE",
            loading_date="2026-07-15",
            products=[
                {
                    "line_id": "line-1",
                    "product_code": "PERIOD",
                    "product_name": "Period Product",
                    "quantity": "1",
                    "unit": "CA",
                }
            ],
        )
    )
    db.commit()

    result = match_arrangement(db, group.id)

    matched = result["items"][0]["matched_product"]
    assert matched["price"] == 20
    assert matched["contract_price"] == 40
    assert matched["purchase_price_period"]["period_id"] == purchase["id"]
    assert matched["selling_price_period"]["period_id"] == selling["id"]


def test_price_period_http_contract(client, db):
    product, _country, _port = _product(db)
    user = seed_user(db, email="price-admin@test", role="admin")
    headers = login(client, user.email)

    created = client.post(
        f"/api/data/products/{product.id}/price-periods",
        headers=headers,
        json={
            "price_type": "selling",
            "amount": 250,
            "effective_from": "2026-01-01",
            "effective_to": "2026-12-31",
        },
    )
    listed = client.get(
        f"/api/data/products/{product.id}/price-periods", headers=headers
    )

    assert created.status_code == 201, created.text
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["price_type"] == "selling"


def test_batch_upload_accepts_separate_period_rows_for_existing_product(db):
    from domains.masterdata.upload import commit_batch, parse_excel, resolve_and_score

    product, country, port = _product(db)
    rows = [
        {
            "product_name": product.product_name_en,
            "product_code": product.code,
            "country": country.name,
            "port": port.name,
            "price": amount,
            "purchase_price_effective_from": start,
            "purchase_price_effective_to": end,
        }
        for amount, start, end in (
            (10, "2026-01-01", "2026-06-30"),
            (20, "2026-07-01", "2026-12-31"),
        )
    ]
    batch = parse_excel(
        db, file_bytes=make_excel(rows), filename="periods.xlsx", user_id=1
    )
    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)

    assert resolved.error_rows == 0
    result = commit_batch(db, batch_id=batch.id, user_id=1)
    periods = (
        db.query(ProductPricePeriod)
        .filter_by(product_id=product.id, price_type="purchase", status=True)
        .order_by(ProductPricePeriod.effective_from)
        .all()
    )
    assert result["updated"] == 2
    assert [(float(row.amount), row.effective_from.isoformat()) for row in periods] == [
        (10.0, "2026-01-01"),
        (20.0, "2026-07-01"),
    ]


def test_batch_upload_creates_one_new_product_with_multiple_periods(db):
    from domains.masterdata.upload import commit_batch, parse_excel, resolve_and_score

    country = Country(name="Upload Japan", code="JP2")
    db.add(country)
    db.flush()
    port = Port(name="Upload Tokyo", code="UTY", country_id=country.id)
    db.add(port)
    db.commit()
    rows = [
        {
            "product_name": "New Period Product",
            "product_code": "NEW-PERIOD",
            "country": country.name,
            "port": port.name,
            "price": amount,
            "purchase_price_effective_from": start,
            "purchase_price_effective_to": end,
        }
        for amount, start, end in (
            (10, "2026-01-01", "2026-06-30"),
            (20, "2026-07-01", "2026-12-31"),
        )
    ]
    batch = parse_excel(
        db, file_bytes=make_excel(rows), filename="new-periods.xlsx", user_id=1
    )
    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)

    assert resolved.error_rows == 0
    result = commit_batch(db, batch_id=batch.id, user_id=1)
    products = db.query(Product).filter_by(code="NEW-PERIOD").all()
    periods = db.query(ProductPricePeriod).filter_by(product_id=products[0].id).all()
    assert result["created"] == 1
    assert len(products) == 1
    assert len(periods) == 2
