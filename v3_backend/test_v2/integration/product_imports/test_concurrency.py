"""Optimistic snapshot checks before an import is committed."""

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from domains.masterdata.models import Product, ProductPricePeriod
from domains.product_imports.commit import ImportConflict, commit_batch
from infrastructure.db.base import Base
from infrastructure.db.model_registry import import_all_models
from test_v2.integration.product_imports.test_validation import (
    price,
    product,
    setup_data,
    validate_upload,
)


def test_commit_refuses_product_changed_after_preview(db):
    japan, osaka, _, _, _ = setup_data(db)
    existing = Product(
        product_name_en="APPLE",
        code="P-200",
        port_id=osaka.id,
        country_id=japan.id,
        brand="OLD",
        status=True,
    )
    db.add(existing)
    db.commit()
    preview = validate_upload(db, [product("P-200", 品牌="FROM FILE")])

    existing.brand = "CHANGED AFTER PREVIEW"
    db.commit()

    with pytest.raises(ImportConflict, match="产品已变化"):
        commit_batch(db, preview.batch_id, user_id=1)
    db.refresh(existing)
    assert existing.brand == "CHANGED AFTER PREVIEW"


def test_commit_refuses_price_period_changed_after_preview(db):
    japan, osaka, _, _, _ = setup_data(db)
    existing = Product(
        product_name_en="APPLE", code="P-201", port_id=osaka.id, country_id=japan.id
    )
    db.add(existing)
    db.flush()
    period = ProductPricePeriod(
        product_id=existing.id,
        price_type="purchase",
        amount=100,
        currency="JPY",
        effective_from=date(2027, 1, 1),
        effective_to=date(2027, 1, 31),
    )
    db.add(period)
    db.commit()
    preview = validate_upload(
        db,
        [],
        [price("P-201", date(2027, 1, 1), date(2027, 1, 31), amount=110)],
    )

    period.amount = 105
    db.commit()

    with pytest.raises(ImportConflict, match="价格区间已变化"):
        commit_batch(db, preview.batch_id, user_id=1)
    db.refresh(period)
    assert float(period.amount) == 105


def test_postgres_conflict_check_preserves_external_product_change(pg_isolated_url):
    import_all_models()
    engine = create_engine(pg_isolated_url)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as prepare:
        japan, osaka, _, _, _ = setup_data(prepare)
        prepare.add(
            Product(
                product_name_en="APPLE",
                code="P-PG",
                port_id=osaka.id,
                country_id=japan.id,
                brand="OLD",
                status=True,
            )
        )
        prepare.commit()
        batch_id = validate_upload(
            prepare, [product("P-PG", 品牌="FROM FILE")]
        ).batch_id
    with sessions() as concurrent:
        row = concurrent.query(Product).filter_by(code="P-PG").one()
        row.brand = "CONCURRENT"
        concurrent.commit()
    with sessions() as submit, pytest.raises(ImportConflict, match="产品已变化"):
        commit_batch(submit, batch_id, user_id=1)
    with sessions() as verify:
        assert verify.query(Product).filter_by(code="P-PG").one().brand == "CONCURRENT"
    engine.dispose()
