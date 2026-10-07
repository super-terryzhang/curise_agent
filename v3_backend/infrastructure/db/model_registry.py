"""Canonical ORM model registry used by Alembic, tests and fresh installs."""

from __future__ import annotations

import importlib

MODEL_MODULES = (
    "domains.identity.models",
    "domains.dynamic_data.models",
    "domains.product_imports.models",
    "domains.masterdata.models",
    "domains.masterdata.images.bulk_models",
    "domains.masterdata.upload.models",
    "domains.document.models",
    "domains.orders.models",
    "domains.orders.oracle_models",
    "domains.orders.financials.models",
    "domains.settings.models",
    "domains.inquiry.models",
    "domains.line.models",
    "agent.storage.models",
)


def import_all_models() -> None:
    """Register every persisted model with ``Base.metadata``.

    Keep this list explicit: an import error must stop migrations and fresh
    installs instead of silently producing a partial schema.
    """

    for module in MODEL_MODULES:
        importlib.import_module(module)
