"""Price changes must be visible before approval and reversible afterwards."""
from decimal import Decimal

import pytest

from domains.masterdata.upload import service
from test_v2.fixtures.helpers import make_excel, seed_product


def staged(db, rows):
    batch = service.parse_excel(db, file_bytes=make_excel(rows), filename="prices.xlsx", user_id=1)
    service.resolve_and_score(db, batch_id=batch.id, user_id=1)
    return batch


@pytest.mark.parametrize("old,new,action", [
    (120, 150, "change"), (120, 0, "change"),
    (120, None, "keep_db"), (120, 120, "unchanged"), (None, 150, "set_new"),
])
def test_selling_price_preview_matches_commit(db, old, new, action):
    product = seed_product(db, code="PRICE", name="Price Test", price=100)
    product.contract_price = Decimal(old) if old is not None else None
    db.commit()
    batch = staged(db, [{"product_name": "Price Test", "product_code": "PRICE", "contract_price": new}])
    preview = service.preview_changes(db, batch_id=batch.id, user_id=1)
    group = "update" if action in {"change", "set_new"} else "skip"
    assert preview["summary"][group] == 1
    assert preview[group][0]["fields"]["contract_price"]["action"] == action
    result = service.commit_batch(db, batch_id=batch.id, user_id=1)
    assert result["updated"] == (group == "update")
    assert product.contract_price == (Decimal(new) if new is not None else Decimal(old))
    assert product.price == Decimal(100)


def test_preview_counts_all_rows_even_when_details_truncated(db):
    rows = []
    for i in range(5):
        seed_product(db, code=f"P{i}", name=f"Item {i}", price=100)
        rows.append({"product_name": f"Item {i}", "product_code": f"P{i}", "price": 100,
                     "contract_price": 150 if i < 3 else None})
    rows.append({"product_name": "New", "contract_price": 77})
    batch = staged(db, rows)
    preview = service.preview_changes(db, batch_id=batch.id, user_id=1, limit=1)
    assert preview["summary"] == {"create": 1, "update": 3, "skip": 2, "error": 0, "total": 6}
    assert len(preview["update"]) == 1 and preview["truncated"]
    assert preview["create"][0]["contract_price"] == 77


@pytest.mark.parametrize("old", [None, 0, 120])
def test_rollback_restores_both_prices_and_is_idempotent(db, old):
    product = seed_product(db, code="PRICE", name="Price Test", price=100)
    product.contract_price = Decimal(old) if old is not None else None
    db.commit()
    batch = staged(db, [{"product_name": "Price Test", "product_code": "PRICE",
                         "price": 80, "contract_price": 150}])
    service.commit_batch(db, batch_id=batch.id, user_id=1)
    result = service.rollback_batch(db, batch_id=batch.id, user_id=1)
    assert result == {"deleted": 0, "restored": 2, "skipped": 0}
    assert product.price == Decimal(100)
    assert product.contract_price == (Decimal(old) if old is not None else None)
    assert batch.status == "rolled_back"
    assert service.rollback_batch(db, batch_id=batch.id, user_id=1) == {
        "deleted": 0, "restored": 0, "skipped": 0,
    }


def test_incomplete_rollback_does_not_claim_rolled_back(db):
    from domains.masterdata.upload.models import ProductChangeLog

    product = seed_product(db, code="PRICE", name="Price Test", price=100)
    batch = staged(db, [{"product_name": "Price Test", "product_code": "PRICE", "price": 80}])
    service.commit_batch(db, batch_id=batch.id, user_id=1)
    db.add(ProductChangeLog(batch_id=batch.id, product_id=product.id, user_id=1,
                            action="update", field_name="unknown", old_value="1", new_value="2"))
    db.commit()
    result = service.rollback_batch(db, batch_id=batch.id, user_id=1)
    assert result["skipped"] == 2
    assert result["restored"] == 0
    assert result["conflicts"][0]["product_id"] == product.id
    db.refresh(product)
    assert product.price == Decimal(80)
    assert batch.status == "completed"


@pytest.mark.parametrize("value,expected", [
    ("1,500", "1500.00"), ("¥1,250.25", "1250.25"),
    (0, "0.00"), ("1.005", "1.01"), ("99999999.99", "99999999.99"),
])
def test_price_parsing_is_identical_for_both_fields(db, value, expected):
    from domains.masterdata.schemas import ProductUpdate

    product = seed_product(db, code="PRICE", name="Price Test", price=100)
    batch = staged(db, [{"product_name": "Price Test", "product_code": "PRICE",
                         "price": value, "contract_price": value}])
    preview = service.preview_changes(db, batch_id=batch.id, user_id=1)
    for field in ("price", "contract_price"):
        assert preview["update"][0]["fields"][field]["excel"] == float(expected)
    result = service.commit_batch(db, batch_id=batch.id, user_id=1)
    assert result["updated"] == 1 and result["errors"] == 0
    assert product.price == product.contract_price == Decimal(expected)
    body = ProductUpdate(price=value, contract_price=value)
    assert body.price == body.contract_price == float(expected)


@pytest.mark.parametrize("field", ["price", "contract_price"])
@pytest.mark.parametrize("value", [-1, "abc", "NaN", "Infinity", "100000000", "1,50", True, "=1+1"])
def test_invalid_price_rejects_entire_row_with_visible_error(db, field, value):
    from pydantic import ValidationError

    from domains.masterdata.schemas import ProductUpdate

    product = seed_product(db, code="PRICE", name="Price Test", price=100)
    product.contract_price = Decimal(120)
    db.commit()
    batch = staged(db, [{"product_name": "Price Test", "product_code": "PRICE",
                         "price": 80, "contract_price": 150, field: value},
                        {"product_name": "Valid New", "price": 42}])
    preview = service.preview_changes(db, batch_id=batch.id, user_id=1)
    assert preview["summary"]["error"] == 1
    assert field in " ".join(preview["error"][0]["errors"])
    result = service.commit_batch(db, batch_id=batch.id, user_id=1)
    assert result["errors"] == 1 and result["created"] == 1 and result["updated"] == 0
    assert product.price == Decimal(100) and product.contract_price == Decimal(120)
    with pytest.raises(ValidationError):
        ProductUpdate(**{field: value})


def test_old_resolved_invalid_batch_is_revalidated_at_preview_and_commit(db):
    from domains.masterdata.upload.models import StagingProduct

    product = seed_product(db, code="PRICE", name="Price Test", price=100)
    batch = staged(db, [{"product_name": "Price Test", "product_code": "PRICE", "contract_price": -5}])
    row = db.query(StagingProduct).filter_by(batch_id=batch.id).one()
    # Simulate a batch resolved by the old version, which allowed negative prices.
    row.match_status = "exact"
    row.match_target_id = product.id
    row.validation_errors = []
    db.commit()
    preview = service.preview_changes(db, batch_id=batch.id, user_id=1)
    assert preview["summary"]["error"] == 1 and preview["summary"]["update"] == 0
    result = service.commit_batch(db, batch_id=batch.id, user_id=1)
    assert result["errors"] == 1 and result["updated"] == 0
    assert product.contract_price is None
