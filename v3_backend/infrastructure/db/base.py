"""SQLAlchemy declarative base.

All domain models inherit from `Base`. Alembic discovers them via
`Base.metadata` — new models must be importable before running migrations.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
