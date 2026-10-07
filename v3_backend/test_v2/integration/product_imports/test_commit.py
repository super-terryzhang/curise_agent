"""Atomic commit behaviour for staged product imports."""

from datetime import date

import pytest
from sqlalchemy import select

from domains.dynamic_data.models import DataRecord
from domains.masterdata.models import Product, ProductPricePeriod
from domains.product_imports.commit import commit_batch
from domains.product_imports.models import ImportBatch, ImportChange
from test_v2.integration.product_imports.test_validation import (
    price,
    product,
    setup_data,
    validate_upload,
)


def test_commit_creates_product_extension_and_prices_once(db):
    setup_data(db)
    preview = validate_upload(
        db,
        [product("P-100", 业务分类="中标产品")],
        [
            price("P-100", date(2027, 1, 1), date(2027, 1, 31), amount=120),
            price(
                "P-100",
                date(2027, 1, 1),
                date(2027, 1, 31),
                kind="卖价",
                amount=180,
            ),
        ],
    )

    result = commit_batch(db, preview.batch_id, user_id=1)
    retry = commit_batch(db, preview.batch_id, user_id=1)

    created = db.scalar(select(Product).where(Product.code == "P-100"))
    assert created is not None
    assert result == retry
    assert result.created == 3
    assert len(db.scalars(select(ProductPricePeriod)).all()) == 2
    anchor = db.scalar(
        select(DataRecord).where(DataRecord.source_record_id == str(created.id))
    )
    assert anchor is not None and anchor.values
    assert len(db.scalars(select(ImportChange)).all()) == 4
    assert db.get(ImportBatch, preview.batch_id).status == "committed"


def test_commit_is_all_or_nothing_when_late_price_write_fails(db, monkeypatch):
    setup_data(db)
    preview = validate_upload(
        db,
        [product("P-ROLLBACK", 业务分类="临时产品")],
        [
            price("P-ROLLBACK", date(2027, 2, 1), date(2027, 2, 28)),
            price(
                "P-ROLLBACK",
                date(2027, 2, 1),
                date(2027, 2, 28),
                kind="卖价",
                amount=160,
            ),
        ],
    )
    import domains.product_imports.commit as module

    original = module._apply_price_row
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated late failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_apply_price_row", fail_second)
    with pytest.raises(RuntimeError, match="late failure"):
        commit_batch(db, preview.batch_id, user_id=1)

    assert db.scalar(select(Product).where(Product.code == "P-ROLLBACK")) is None
    assert db.scalars(select(ProductPricePeriod)).all() == []
    assert db.scalars(select(ImportChange)).all() == []
    assert db.get(ImportBatch, preview.batch_id).status == "ready"
