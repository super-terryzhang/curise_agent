"""SQLAlchemy engine — single instance for the process."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from infrastructure.config import settings


def _build_engine() -> Engine:
    url = settings.DATABASE_URL
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        # SQLite needs check_same_thread=False for FastAPI's threaded test client
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_size"] = 5
        kwargs["max_overflow"] = 10
    return create_engine(url, **kwargs)


engine: Engine = _build_engine()
