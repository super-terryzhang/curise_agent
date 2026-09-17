"""Alembic environment — resolves DB URL from v3 settings and imports all domain models.

IMPORTANT: every new domain MUST be added to `_import_all_models()` below so
Alembic sees its tables in `Base.metadata` and can autogenerate migrations.
"""

from __future__ import annotations

import importlib
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# v3 imports
from infrastructure.config import settings
from infrastructure.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _import_all_models() -> None:
    """Import every domain's models so they register with `Base.metadata`.

    Every nested package that defines its OWN tables (not just FK-linked
    rows) must be listed here. Forgetting one means autogen won't see its
    tables and a future migration silently skips it — which is exactly
    how `v3_upload_batches` slipped into prod without a migration before
    0010 backfilled it.
    """
    for module in (
        "domains.identity.models",
        "domains.masterdata.models",
        "domains.masterdata.images.bulk_models",
        "domains.masterdata.upload.models",  # added 0010_upload_pipeline
        "domains.document.models",
        "domains.orders.models",
        "domains.settings.models",
        "domains.inquiry.models",
        "domains.line.models",
    ):
        importlib.import_module(module)


_import_all_models()

target_metadata = Base.metadata


def _get_url() -> str:
    return settings.DATABASE_URL


def run_migrations_offline() -> None:
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section) or {}
    cfg["sqlalchemy.url"] = _get_url()
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
