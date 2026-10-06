"""Dedicated foreign-key-enabled database; existing fixtures remain unchanged."""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from infrastructure.db.base import Base


@pytest.fixture
def data_db():
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(
        engine, tables=[t for n, t in Base.metadata.tables.items() if n.startswith("v3_data_")]
    )
    with Session(engine) as db:
        yield db
    engine.dispose()
