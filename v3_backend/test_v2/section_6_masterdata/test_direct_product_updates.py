"""Direct on-page edits reuse staging and never write before confirmation."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from domains.masterdata.models import Country, Port, Product, ProductPricePeriod, Supplier
from domains.masterdata.upload import commit_validated_batch, get_workflow_rows, rollback_batch
from domains.masterdata.upload.errors import BatchValidationFailed
from test_v2.fixtures.helpers import seed_product


def _prepare(db, products, rows, scope="purchase", operation="edit"):
    from domains.masterdata.upload.direct_updates import DirectUpdateRequest, prepare_direct_update

    return prepare_direct_update(
        db,
        DirectUpdateRequest(
            selected_product_ids=[p.id for p in products],
            scope=scope,
            operation=operation,
            rows=rows,
        ),
        user_id=1,
    )


def _period(db, product, amount=10, start="2026-04-01", end="2026-06-30", kind="purchase"):
    period = ProductPricePeriod(
        product_id=product.id,
        price_type=kind,
        amount=Decimal(str(amount)),
        currency="JPY",
        effective_from=date.fromisoformat(start),
        effective_to=date.fromisoformat(end),
        status=True,
        source="test",
    )
    db.add(period)
    db.commit()
    return period


def _row(product, values, period_id=None):
    return {
        "product_id": product.id,
        "expected_revision": product.revision,
        "period_id": period_id,
        "values": values,
    }


def test_uniform_end_date_preserves_each_products_amount_and_preview_is_read_only(db):
    first = seed_product(db, code="A", name="A", price=12)
    second = seed_product(db, code="B", name="B", price=78)
    p1, p2 = _period(db, first, 12), _period(db, second, 78)
    snapshot = _prepare(
        db,
        [first, second],
        [
            _row(first, {"effective_to": "2026-07-31"}, p1.id),
            _row(second, {"effective_to": "2026-07-31"}, p2.id),
        ],
    )
    assert snapshot["can_continue"] is True
    assert snapshot["workflow_version"] == 3
    assert p1.effective_to == date(2026, 6, 30)
    rows = get_workflow_rows(db, batch_id=snapshot["id"], user_id=1)["items"]
    assert len(rows) == 2
    assert all(item["operations"] == ["更新采购价区间"] for item in rows)
    result = commit_validated_batch(db, batch_id=snapshot["id"], user_id=1)
    assert result["updated"] == 2
    assert result["created"] == 0
    db.refresh(p1)
    db.refresh(p2)
    assert (p1.amount, p2.amount) == (Decimal("12"), Decimal("78"))
    assert p1.effective_to == p2.effective_to == date(2026, 7, 31)
    assert first.price == Decimal("12")
    rollback_batch(db, batch_id=snapshot["id"], user_id=1)
    db.refresh(p1)
    assert p1.effective_to == date(2026, 6, 30)


@pytest.mark.parametrize("scope", ["purchase", "selling"])
def test_new_multiple_periods_do_not_replace_existing_prices(db, scope):
    product = seed_product(db)
    old = _period(db, product, 10, kind=scope)
    snapshot = _prepare(
        db,
        [product],
        [
            _row(
                product,
                {
                    "amount": "0",
                    "currency": "JPY",
                    "effective_from": "2026-08-01",
                    "effective_to": "2026-09-30",
                },
            ),
            _row(
                product,
                {
                    "amount": "20",
                    "currency": "JPY",
                    "effective_from": "2026-10-01",
                    "effective_to": "2026-12-31",
                },
            ),
        ],
        scope,
        "add",
    )
    assert snapshot["can_continue"] is True
    commit_validated_batch(db, batch_id=snapshot["id"], user_id=1)
    periods = db.query(ProductPricePeriod).filter_by(product_id=product.id, price_type=scope).all()
    assert len(periods) == 3
    assert old.amount == Decimal("10")
    assert sorted(p.amount for p in periods) == [Decimal("0"), Decimal("10"), Decimal("20")]


def test_basic_identity_changes_use_original_id_and_have_reversible_diff(db):
    product = seed_product(db, code="0001", name="Original")
    supplier = Supplier(name="Supplier A")
    db.add(supplier)
    db.commit()
    snapshot = _prepare(
        db,
        [product],
        [
            _row(
                product,
                {
                    "product_name_en": "Renamed",
                    "code": "0002",
                    "supplier_id": supplier.id,
                },
            )
        ],
        "basic",
    )
    assert snapshot["can_continue"] is True
    assert product.product_name_en == "Original"
    rows = get_workflow_rows(db, batch_id=snapshot["id"], user_id=1)["items"]
    diff = {f["key"]: f for f in rows[0]["fields"]}
    assert diff["product_name_en"]["after"] == "Renamed"
    assert diff["supplier_id"]["after"] == "Supplier A"
    commit_validated_batch(db, batch_id=snapshot["id"], user_id=1)
    db.refresh(product)
    assert product.product_name_en == "Renamed"
    assert product.code == "0002"
    assert db.query(Product).count() == 1
    rollback_batch(db, batch_id=snapshot["id"], user_id=1)
    db.refresh(product)
    assert (product.product_name_en, product.code, product.supplier_id) == (
        "Original",
        "0001",
        None,
    )


@pytest.mark.parametrize(
    "case", ["unselected", "scope", "missing_product", "wrong_period", "missing_price", "unknown"]
)
def test_rejects_requests_outside_operation_boundary(db, case):
    first = seed_product(db, code="A", name="A")
    other = seed_product(db, code="B", name="B")
    period = _period(db, other)
    row = _row(
        first, {"amount": "25", "effective_from": "2026-08-01", "effective_to": "2026-09-30"}
    )
    scope, operation = "purchase", "add"
    if case == "unselected":
        row["product_id"] = other.id
    if case == "scope":
        row["values"] = {"supplier_id": 1}
    if case == "missing_product":
        row["product_id"] = 99999
    if case == "wrong_period":
        row["period_id"], operation = period.id, "edit"
    if case == "missing_price":
        row["values"].pop("amount")
    if case == "unknown":
        row["values"]["future_field"] = "x"
    with pytest.raises(ValueError):
        _prepare(db, [first], [row], scope, operation)
    assert first.price == Decimal("10")


def test_overlap_returns_concrete_row_issue_and_commit_is_blocked(db):
    product = seed_product(db)
    _period(db, product, 10, "2026-07-01", "2026-09-30")
    snapshot = _prepare(
        db,
        [product],
        [
            _row(
                product,
                {
                    "amount": "15",
                    "effective_from": "2026-08-01",
                    "effective_to": "2026-10-31",
                },
            )
        ],
        operation="add",
    )
    assert snapshot["can_continue"] is False
    issues = get_workflow_rows(db, batch_id=snapshot["id"], user_id=1, view="issues")["items"]
    assert "2026-07-01" in issues[0]["issues"][0]["message"]
    with pytest.raises(BatchValidationFailed):
        commit_validated_batch(db, batch_id=snapshot["id"], user_id=1)
    assert db.query(ProductPricePeriod).count() == 1


def test_preview_then_concurrent_change_rejects_entire_direct_batch(db):
    first = seed_product(db, code="A", name="A")
    second = seed_product(db, code="B", name="B")
    snapshot = _prepare(
        db,
        [first, second],
        [
            _row(first, {"brand": "New A"}),
            _row(second, {"brand": "New B"}),
        ],
        "basic",
    )
    second.revision += 1
    db.commit()
    with pytest.raises(BatchValidationFailed):
        commit_validated_batch(db, batch_id=snapshot["id"], user_id=1)
    db.refresh(first)
    assert first.brand is None


def test_unique_identity_collision_rejected_without_partial_save(db):
    first = seed_product(db, code="A", name="A")
    second = seed_product(db, code="B", name="B")
    snapshot = _prepare(
        db,
        [first, second],
        [
            _row(first, {"brand": "New"}),
            _row(second, {"code": "A", "product_name_en": "A"}),
        ],
        "basic",
    )
    assert snapshot["can_continue"] is False
    with pytest.raises(BatchValidationFailed):
        commit_validated_batch(db, batch_id=snapshot["id"], user_id=1)
    db.refresh(first)
    assert first.brand is None


def test_basic_location_and_clear_are_previewed_saved_and_rolled_back_without_price_changes(db):
    product = seed_product(db, code="A", name="A")
    product.brand = "Old brand"
    old_country, old_port = product.country_id, product.port_id
    period = _period(db, product)
    country = Country(name="New Country", code="NC")
    db.add(country)
    db.flush()
    port = Port(name="New Port", country_id=country.id)
    db.add(port)
    db.commit()
    snapshot = _prepare(
        db,
        [product],
        [
            _row(
                product,
                {
                    "country_id": country.id,
                    "port_id": port.id,
                    "brand": None,
                },
            )
        ],
        "basic",
    )
    assert snapshot["can_continue"] is True
    diff = {
        f["key"]: f
        for f in get_workflow_rows(db, batch_id=snapshot["id"], user_id=1)["items"][0]["fields"]
    }
    assert diff["country_id"]["after"] == "New Country"
    assert diff["port_id"]["after"] == "New Port"
    assert diff["brand"]["after"] is None
    commit_validated_batch(db, batch_id=snapshot["id"], user_id=1)
    db.refresh(product)
    assert (product.country_id, product.port_id, product.brand) == (country.id, port.id, None)
    assert db.query(ProductPricePeriod).count() == 1
    assert period.amount == Decimal("10")
    rollback_batch(db, batch_id=snapshot["id"], user_id=1)
    db.refresh(product)
    assert (product.country_id, product.port_id, product.brand) == (
        old_country,
        old_port,
        "Old brand",
    )


def test_direct_row_write_failure_rolls_back_previous_rows(db, monkeypatch):
    from domains.masterdata.upload import service

    first = seed_product(db, code="A", name="A")
    second = seed_product(db, code="B", name="B")
    snapshot = _prepare(
        db,
        [first, second],
        [_row(first, {"brand": "New A"}), _row(second, {"brand": "New B"})],
        "basic",
    )
    original = service._apply_update

    applied = []

    def failing_update(db, target, sp, batch_id, user_id):
        if sp.match_target_id == second.id:
            raise ValueError("Synthetic write failure")
        result = original(db, target, sp, batch_id, user_id)
        applied.append(target.id)
        return result

    monkeypatch.setattr(service, "_apply_update", failing_update)
    with pytest.raises(BatchValidationFailed):
        commit_validated_batch(db, batch_id=snapshot["id"], user_id=1)
    db.refresh(first)
    assert first.brand is None
    assert applied == [first.id]


def test_basic_edit_never_resynchronizes_legacy_prices_over_canonical_periods(db):
    product = seed_product(db, price=10)
    product.purchase_price_effective_from = datetime(2026, 4, 1)
    product.purchase_price_effective_to = datetime(2026, 6, 30)
    db.commit()
    period = _period(db, product, amount=99)
    snapshot = _prepare(db, [product], [_row(product, {"brand": "Brand A"})], "basic")
    assert snapshot["can_continue"] is True
    commit_validated_batch(db, batch_id=snapshot["id"], user_id=1)
    db.refresh(period)
    assert period.amount == Decimal("99")
    rollback_batch(db, batch_id=snapshot["id"], user_id=1)
    db.refresh(period)
    assert period.amount == Decimal("99")


def test_existing_ambiguous_names_do_not_block_unrelated_basic_edit(db):
    first = seed_product(db, code="A", name="Same name")
    seed_product(db, code="B", name="Same name")
    snapshot = _prepare(db, [first], [_row(first, {"brand": "Brand A"})], "basic")
    assert snapshot["can_continue"] is True


def test_identity_rollback_refuses_identity_reused_by_another_product(db):
    first = seed_product(db, code="A", name="A")
    second = seed_product(db, code="B", name="B")
    original = _prepare(db, [first], [_row(first, {"code": "X", "product_name_en": "X"})], "basic")
    commit_validated_batch(db, batch_id=original["id"], user_id=1)
    later = _prepare(db, [second], [_row(second, {"code": "A", "product_name_en": "A"})], "basic")
    commit_validated_batch(db, batch_id=later["id"], user_id=1)
    result = rollback_batch(db, batch_id=original["id"], user_id=1)
    assert result["skipped"] == 2
    assert "重复" in result["conflicts"][0]["reason"]
    db.refresh(first)
    assert (first.code, first.product_name_en) == ("X", "X")


def test_direct_identity_swap_can_be_rolled_back_as_one_batch(db):
    first = seed_product(db, code="A", name="A")
    second = seed_product(db, code="B", name="B")
    snapshot = _prepare(
        db,
        [first, second],
        [
            _row(first, {"code": "B", "product_name_en": "B"}),
            _row(second, {"code": "A", "product_name_en": "A"}),
        ],
        "basic",
    )
    assert snapshot["can_continue"] is True
    commit_validated_batch(db, batch_id=snapshot["id"], user_id=1)
    result = rollback_batch(db, batch_id=snapshot["id"], user_id=1)
    assert result["skipped"] == 0
    db.refresh(first)
    db.refresh(second)
    assert (first.code, second.code) == ("A", "B")


def test_direct_rollback_conflict_restores_none_of_the_batch(db):
    first = seed_product(db, code="A", name="A")
    second = seed_product(db, code="B", name="B")
    snapshot = _prepare(
        db,
        [first, second],
        [_row(first, {"brand": "New A"}), _row(second, {"brand": "New B"})],
        "basic",
    )
    commit_validated_batch(db, batch_id=snapshot["id"], user_id=1)
    second.brand = "Later edit"
    db.commit()
    result = rollback_batch(db, batch_id=snapshot["id"], user_id=1)
    assert (result["restored"], result["skipped"]) == (0, 2)
    db.refresh(first)
    assert first.brand == "New A"
