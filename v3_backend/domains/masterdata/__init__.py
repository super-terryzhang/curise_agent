"""Master data domain — countries, ports, categories, suppliers, products, exchange rates.

Public API:
- `service`: business operations (list_*, create_*, update_*, delete_*)
- `schemas`: Pydantic DTOs
- `models`: ORM models (for Alembic discovery — other domains must NOT import)
"""

from domains.masterdata import schemas, service
from domains.masterdata.models import (
    Category,
    Country,
    ExchangeRate,
    Port,
    Product,
    Supplier,
    SupplierCategory,
    UnitConversionRule,
)

__all__ = [
    "service",
    "schemas",
    "Country",
    "Port",
    "Category",
    "Supplier",
    "SupplierCategory",
    "Product",
    "UnitConversionRule",
    "ExchangeRate",
]
