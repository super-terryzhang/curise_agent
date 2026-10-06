"""Storage contracts exercised on a foreign-key-enabled isolated database."""

from importlib import import_module
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domains.masterdata.models import Product
from infrastructure.db.base import Base


def models():
    return import_module("domains.dynamic_data.models")


def test_six_models_register_and_keep_existing_product_columns():
    before = [(c.name, str(c.type), c.nullable) for c in Product.__table__.columns]
    module = models()
    tables = [t for t in Base.metadata.tables if t.startswith("v3_data_")]
    assert set(tables) == {
        "v3_data_tables",
        "v3_data_fields",
        "v3_data_records",
        "v3_data_links",
        "v3_data_unique_values",
        "v3_data_changes",
    }
    assert len(tables) == 6
    assert [(c.name, str(c.type), c.nullable) for c in Product.__table__.columns] == before
    assert module.DataTable.__table__.c.schema_version.default.arg == 1


@pytest.mark.parametrize("wrong", ["field", "source", "target", "target_table"])
def test_links_cannot_use_other_tables_field_or_target(wrong):
    m = models()
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(
        engine,
        tables=[
            Base.metadata.tables[n]
            for n in (
                "v3_data_tables",
                "v3_data_fields",
                "v3_data_records",
                "v3_data_links",
                "v3_data_unique_values",
                "v3_data_changes",
            )
        ],
    )
    a, b, c, f, g, source, target, other = [uuid4() for _ in range(8)]
    with Session(engine) as db:
        db.add_all([m.DataTable(id=i, name=str(i), created_by=1, updated_by=1) for i in (a, b, c)])
        db.flush()
        db.add_all(
            [
                m.DataField(id=f, table_id=a, label="联系", field_type="link", target_table_id=b),
                m.DataField(
                    id=g, table_id=c, label="其他联系", field_type="link", target_table_id=b
                ),
                m.DataRecord(id=source, table_id=a, values={}, created_by=1, updated_by=1),
                m.DataRecord(id=target, table_id=b, values={}, created_by=1, updated_by=1),
                m.DataRecord(id=other, table_id=c, values={}, created_by=1, updated_by=1),
            ]
        )
        db.commit()
        args = {
            "table_id": a,
            "record_id": source,
            "field_id": f,
            "target_table_id": b,
            "target_record_id": target,
        }
        args.update(
            {
                "field": {"field_id": g},
                "source": {"record_id": other},
                "target": {"target_record_id": other},
                "target_table": {"target_table_id": c, "target_record_id": other},
            }[wrong]
        )
        db.add(m.DataLink(**args))
        with pytest.raises(IntegrityError):
            db.commit()
    engine.dispose()


def test_requests_forbid_unknown_system_fields():
    schemas = import_module("domains.dynamic_data.schemas")
    with pytest.raises(ValidationError):
        schemas.RecordCreate(id=uuid4(), schema_version=1, values={}, created_at="2026-10-06")
    for name in ("", "   ", "x" * 101):
        with pytest.raises(ValidationError):
            schemas.TableCreate(id=uuid4(), name=name)
