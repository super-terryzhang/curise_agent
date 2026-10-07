"""Storage constraints for the isolated temporary product-import workflow."""

from importlib import import_module
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domains.masterdata.models import ProductPricePeriod
from infrastructure.db.base import Base


def import_models():
    return import_module("domains.product_imports.models")


def sqlite_engine():
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    return engine


def test_import_models_register_three_isolated_tables_and_price_period_revision():
    import_models()
    assert {
        name for name in Base.metadata.tables if name.startswith("v3_import_")
    } == {"v3_import_batches", "v3_import_rows", "v3_import_changes"}
    revision = ProductPricePeriod.__table__.c.revision
    assert revision.nullable is False
    assert revision.default.arg == 1
    assert str(revision.server_default.arg) == "1"


def test_batch_status_and_staged_source_coordinate_are_database_constraints():
    models = import_models()
    engine = sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[Base.metadata.tables[name] for name in (
            "v3_import_batches", "v3_import_rows", "v3_import_changes"
        )],
    )
    with Session(engine) as db:
        batch = models.ImportBatch(
            id=uuid4(),
            user_id=7,
            filename="产品.xlsx",
            file_sha256="a" * 64,
            contract_version=1,
            product_schema_version=2,
            status="uploaded",
        )
        db.add(batch)
        db.flush()
        db.add_all(
            [
                models.ImportRow(
                    id=uuid4(),
                    batch_id=batch.id,
                    sheet_key="products",
                    source_row_number=2,
                    raw_values={"产品代码": "A"},
                    normalized_values={},
                    action="pending",
                    issues=[],
                ),
                models.ImportRow(
                    id=uuid4(),
                    batch_id=batch.id,
                    sheet_key="prices",
                    source_row_number=2,
                    raw_values={"产品代码": "A"},
                    normalized_values={},
                    action="pending",
                    issues=[],
                ),
            ]
        )
        db.commit()

        db.add(
            models.ImportRow(
                id=uuid4(),
                batch_id=batch.id,
                sheet_key="products",
                source_row_number=2,
                raw_values={},
                normalized_values={},
                action="pending",
                issues=[],
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        with pytest.raises(IntegrityError):
            db.execute(
                text("UPDATE v3_import_batches SET status='unknown' WHERE id=:id"),
                {"id": batch.id.hex},
            )
            db.commit()
    engine.dispose()
