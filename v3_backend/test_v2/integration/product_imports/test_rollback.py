"""Audited and dependency-safe rollback of committed imports."""

from datetime import date

import pytest
from sqlalchemy import select

from domains.dynamic_data.models import DataRecord
from domains.masterdata.models import Product, ProductPricePeriod
from domains.product_imports.commit import ImportConflict, commit_batch, rollback_batch
from test_v2.integration.product_imports.test_validation import (
    price,
    product,
    setup_data,
    validate_upload,
)


def test_rollback_archives_rows_created_only_by_batch_and_is_idempotent(db):
    setup_data(db)
    preview = validate_upload(
        db,
        [product("P-300", 业务分类="未中标产品")],
        [price("P-300", date(2027, 3, 1), date(2027, 3, 31))],
    )
    commit_batch(db, preview.batch_id, user_id=1)

    result = rollback_batch(db, preview.batch_id, user_id=1)
    retry = rollback_batch(db, preview.batch_id, user_id=1)

    created = db.scalar(select(Product).where(Product.code == "P-300"))
    period = db.scalar(select(ProductPricePeriod).where(ProductPricePeriod.product_id == created.id))
    anchor = db.scalar(select(DataRecord).where(DataRecord.source_record_id == str(created.id)))
    assert result == retry
    assert created.status is False
    assert period.status is False
    assert anchor.status == "archived"


def test_rollback_refuses_when_committed_product_has_later_change(db):
    setup_data(db)
    preview = validate_upload(db, [product("P-301", 业务分类="临时产品")])
    commit_batch(db, preview.batch_id, user_id=1)
    created = db.scalar(select(Product).where(Product.code == "P-301"))
    created.brand = "LATER CHANGE"
    db.commit()

    with pytest.raises(ImportConflict, match="后续修改"):
        rollback_batch(db, preview.batch_id, user_id=1)
    db.refresh(created)
    assert created.status is True
    assert created.brand == "LATER CHANGE"


def test_price_only_rollback_restores_product_price_version(db):
    japan, osaka, _, _, _ = setup_data(db)
    existing = Product(
        product_name_en="APPLE",
        code="P-302",
        port_id=osaka.id,
        country_id=japan.id,
        status=True,
    )
    db.add(existing)
    db.commit()
    original_price_version = existing.price_version
    preview = validate_upload(
        db,
        [],
        [price("P-302", date(2027, 4, 1), date(2027, 4, 30))],
    )

    commit_batch(db, preview.batch_id, user_id=1)
    db.refresh(existing)
    assert existing.price_version == original_price_version + 1

    rollback_batch(db, preview.batch_id, user_id=1)
    db.refresh(existing)
    assert existing.price_version == original_price_version
