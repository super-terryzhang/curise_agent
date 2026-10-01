"""Multiple non-overlapping price intervals selected by loading day."""

from datetime import date, datetime

import pytest

from domains.masterdata.errors import Conflict
from domains.masterdata.models import Country, Port, Product, ProductPricePeriod, Supplier
from domains.masterdata.price_periods import attach_effective_prices, create_period
from domains.masterdata.schemas import ProductPricePeriodCreate
from domains.masterdata.upload.models import StagingProduct
from domains.orders import anomaly
from domains.orders.groups.matching import match_arrangement
from domains.orders.matching.automation import match_for_import
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
    assert product._effective_prices["purchase"]["warning"] == "采购价期间未配置，需要复核"
    assert product._effective_prices["selling"]["warning"] == "卖价期间未配置"


def test_configured_gap_does_not_fall_back_to_legacy_price(db):
    product, _country, _port = _product(db)
    _create(db, product, "purchase", 10, "2026-01-01", "2026-01-31")

    attach_effective_prices(db, [product], date(2026, 8, 1))

    selected = product._effective_prices["purchase"]
    assert selected["amount"] is None
    assert selected["source"] == "period"
    assert "未命中采购价期间" in selected["warning"]
    assert "需要复核" in selected["warning"]


def test_purchase_period_gap_is_a_non_actionable_warning():
    findings = anomaly.findings_for_order_row(
        {
            "line_id": "line-1",
            "match_status": "matched",
            "matched_product": {
                "id": 1,
                "supplier_id": 2,
                "purchase_price_period": {
                    "source": "period",
                    "amount": None,
                    "warning": "装船日 2026-08-01 未命中采购价期间，需要复核",
                },
                "selling_price_period": {},
            },
        }
    )

    purchase = next(
        item for item in findings if item["code"] == "PURCHASE_PRICE_PERIOD_MISSING"
    )
    assert purchase["severity"] == "warning"
    assert anomaly.is_current_actionable_finding(purchase) is False


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


def test_oracle_matching_selects_purchase_price_by_loading_not_delivery_day(db):
    product, country, port = _product(db)
    _create(db, product, "purchase", 10, "2026-01-01", "2026-01-31")
    loading_period = _create(db, product, "purchase", 20, "2026-07-01", "2026-07-31")
    user = seed_user(db, email="oracle-price-day@test")
    order = Order(
        user_id=user.id,
        filename="oracle.pdf",
        country_id=country.id,
        port_id=port.id,
        delivery_date="2026-01-15",
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
    db.add(order)
    db.commit()

    assert match_for_import(db, order) == []
    matched = order.match_results[0]["matched_product"]
    assert matched["price"] == 20
    assert matched["purchase_price_period"]["period_id"] == loading_period["id"]


def test_missing_loading_day_keeps_match_and_requests_price_review(db):
    product, country, port = _product(db)
    _create(db, product, "purchase", 10, "2026-01-01", "2026-01-31")
    user = seed_user(db, email="oracle-price-review@test")
    order = Order(
        user_id=user.id,
        filename="oracle-missing-loading.pdf",
        country_id=country.id,
        port_id=port.id,
        delivery_date="2026-01-15",
        loading_date=None,
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
    db.add(order)
    db.commit()

    assert match_for_import(db, order) == []
    result = order.match_results[0]
    assert result["match_status"] == "matched"
    purchase = result["matched_product"]["purchase_price_period"]
    assert purchase["amount"] is None
    assert "缺少装船日" in purchase["warning"]
    assert "需要复核" in purchase["warning"]


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


def test_batch_upload_rejects_same_purchase_range_with_different_amounts_before_commit(db):
    from domains.masterdata.upload import parse_excel, resolve_and_score

    product, country, port = _product(db)
    rows = [
        {
            "product_name": product.product_name_en,
            "product_code": product.code,
            "country": country.name,
            "port": port.name,
            "price": amount,
            "purchase_price_effective_from": "2026-01-01",
            "purchase_price_effective_to": "2026-06-30",
        }
        for amount in (10, 20)
    ]
    batch = parse_excel(
        db, file_bytes=make_excel(rows), filename="same-range.xlsx", user_id=1
    )

    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)

    assert resolved.error_rows == 2
    staged = db.query(StagingProduct).filter_by(batch_id=batch.id).all()
    messages = [
        message
        for row in staged
        for message in (row.validation_errors or [])
    ]
    assert any("同一日期区间只能有一个价格" in message for message in messages)
    assert db.query(ProductPricePeriod).filter_by(product_id=product.id).count() == 0


def test_batch_upload_rejects_workbook_overlap_before_commit(db):
    from domains.masterdata.upload import parse_excel, resolve_and_score

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
            (20, "2026-06-15", "2026-12-31"),
        )
    ]
    batch = parse_excel(
        db, file_bytes=make_excel(rows), filename="overlap.xlsx", user_id=1
    )

    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)

    assert resolved.error_rows == 2
    staged = db.query(StagingProduct).filter_by(batch_id=batch.id).all()
    assert all(
        any("期间重叠" in message for message in (row.validation_errors or []))
        for row in staged
    )


def test_batch_upload_rejects_overlap_with_existing_period_before_commit(db):
    from domains.masterdata.upload import parse_excel, resolve_and_score

    product, country, port = _product(db)
    _create(db, product, "purchase", 10, "2026-01-01", "2026-06-30")
    batch = parse_excel(
        db,
        file_bytes=make_excel(
            [
                {
                    "product_name": product.product_name_en,
                    "product_code": product.code,
                    "country": country.name,
                    "port": port.name,
                    "price": 20,
                    "purchase_price_effective_from": "2026-06-15",
                    "purchase_price_effective_to": "2026-12-31",
                }
            ]
        ),
        filename="db-overlap.xlsx",
        user_id=1,
    )

    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)

    assert resolved.error_rows == 1
    staged = db.query(StagingProduct).filter_by(batch_id=batch.id).one()
    assert "与数据库现有区间" in " ".join(staged.validation_errors or [])
    assert db.query(ProductPricePeriod).filter_by(product_id=product.id).count() == 1


def test_batch_upload_updates_exported_period_id_and_rolls_back(db):
    from domains.masterdata import price_history
    from domains.masterdata.upload import (
        commit_validated_batch,
        get_workflow_rows,
        parse_excel,
        resolve_and_score,
        rollback_batch,
    )

    product, country, port = _product(db)
    price_history.initial(db, product, actor_id=None, source="test")
    db.commit()
    created = _create(db, product, "purchase", 10, "2026-01-01", "2026-06-30")
    product.purchase_price_effective_from = datetime(2026, 1, 1)
    product.purchase_price_effective_to = datetime(2026, 6, 30)
    db.commit()
    db.refresh(product)
    batch = parse_excel(
        db,
        file_bytes=make_excel(
            [
                {
                    "product_id": product.id,
                    "expected_revision": product.revision,
                    "product_name": product.product_name_en,
                    "product_code": product.code,
                    "country": country.name,
                    "port": port.name,
                    "purchase_price_period_id": created["id"],
                    "price": 12,
                    "currency": "USD",
                    "purchase_price_effective_from": "2026-02-01",
                    "purchase_price_effective_to": "2026-07-31",
                }
            ]
        ),
        filename="exported-period-update.xlsx",
        user_id=1,
    )

    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)
    assert resolved.error_rows == 0
    preview = get_workflow_rows(
        db, batch_id=batch.id, user_id=1, view="changes", changed_only=True
    )
    assert "更新采购价区间" in preview["items"][0]["operations"]
    assert "更新产品" not in preview["items"][0]["operations"]

    result = commit_validated_batch(db, batch_id=batch.id, user_id=1)
    assert result["errors"] == 0
    updated = db.get(ProductPricePeriod, created["id"])
    assert float(updated.amount) == 12
    assert updated.effective_from == date(2026, 2, 1)
    assert updated.effective_to == date(2026, 7, 31)
    assert updated.currency == "USD"
    db.refresh(product)
    assert float(product.price) == 99
    assert product.currency == "JPY"
    assert product.purchase_price_effective_from.date() == date(2026, 1, 1)
    assert product.purchase_price_effective_to.date() == date(2026, 6, 30)

    rolled_back = rollback_batch(db, batch_id=batch.id, user_id=1)
    assert rolled_back["skipped"] == 0
    restored = db.get(ProductPricePeriod, created["id"])
    assert float(restored.amount) == 10
    assert restored.currency == "JPY"
    assert restored.effective_from == date(2026, 1, 1)
    assert restored.effective_to == date(2026, 6, 30)


def test_batch_upload_adds_canonical_period_without_replacing_fallback_price(db):
    from domains.masterdata import price_history
    from domains.masterdata.upload import (
        commit_validated_batch,
        parse_excel,
        resolve_and_score,
        rollback_batch,
    )

    product, country, port = _product(db)
    price_history.initial(db, product, actor_id=None, source="test")
    db.commit()
    original_price_version = product.price_version
    batch = parse_excel(
        db,
        file_bytes=make_excel(
            [
                {
                    "product_id": product.id,
                    "expected_revision": product.revision,
                    "product_name": product.product_name_en,
                    "product_code": product.code,
                    "country": country.name,
                    "port": port.name,
                    "price": 12,
                    "purchase_price_effective_from": "2026-02-01",
                    "purchase_price_effective_to": "2026-07-31",
                }
            ]
        ),
        filename="exported-period-create.xlsx",
        user_id=1,
    )

    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)
    assert resolved.error_rows == 0
    result = commit_validated_batch(db, batch_id=batch.id, user_id=1)
    assert result["errors"] == 0

    db.refresh(product)
    assert float(product.price) == 99
    assert product.purchase_price_effective_from is None
    assert product.purchase_price_effective_to is None
    assert product.price_version == original_price_version + 1
    added = db.query(ProductPricePeriod).filter_by(
        product_id=product.id,
        price_type="purchase",
        source_batch_id=batch.id,
    ).one()
    assert float(added.amount) == 12
    added_id = added.id

    rolled_back = rollback_batch(db, batch_id=batch.id, user_id=1)
    assert rolled_back["skipped"] == 0
    assert db.get(ProductPricePeriod, added_id) is None


def test_batch_upload_rejects_period_update_without_stable_period_id(db):
    from domains.masterdata.upload import parse_excel, resolve_and_score

    product, country, port = _product(db)
    _create(db, product, "purchase", 10, "2026-01-01", "2026-06-30")
    batch = parse_excel(
        db,
        file_bytes=make_excel(
            [
                {
                    "product_name": product.product_name_en,
                    "product_code": product.code,
                    "country": country.name,
                    "port": port.name,
                    "price": 12,
                    "purchase_price_effective_from": "2026-01-01",
                    "purchase_price_effective_to": "2026-06-30",
                }
            ]
        ),
        filename="ambiguous-period-update.xlsx",
        user_id=1,
    )

    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)

    assert resolved.error_rows == 1
    staged = db.query(StagingProduct).filter_by(batch_id=batch.id).one()
    assert "price_period_id" in " ".join(staged.validation_errors or [])


def test_batch_upload_rejects_wrong_or_stale_export_identity(db):
    from domains.masterdata.upload import parse_excel, resolve_and_score

    product, country, port = _product(db)
    other = Product(
        product_name_en="Other Product",
        code="OTHER",
        country_id=country.id,
        port_id=port.id,
        status=True,
    )
    db.add(other)
    db.commit()
    batch = parse_excel(
        db,
        file_bytes=make_excel(
            [
                {
                    "product_id": product.id,
                    "expected_revision": product.revision + 1,
                    "product_name": other.product_name_en,
                    "product_code": other.code,
                    "country": country.name,
                    "port": port.name,
                    "price": 12,
                }
            ]
        ),
        filename="stale-export.xlsx",
        user_id=1,
    )

    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)

    assert resolved.error_rows == 1
    staged = db.query(StagingProduct).filter_by(batch_id=batch.id).one()
    messages = " ".join(staged.validation_errors or [])
    assert "版本" in messages
    assert "身份" in messages
