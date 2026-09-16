"""Settings domain — admin-configurable rows (field schemas, templates,
delivery locations, company config). See ADR-0001 / ADR-0006.

Public API:
- `service`: business operations
- `schemas`: Pydantic DTOs
- ORM models exposed for Alembic discovery only
"""

from domains.settings import schemas, service
from domains.settings.models import (
    CompanyConfig,
    DeliveryLocation,
    FieldDefinition,
    FieldSchema,
    OrderFormatTemplate,
)

__all__ = [
    "service",
    "schemas",
    "FieldSchema",
    "FieldDefinition",
    "OrderFormatTemplate",
    "DeliveryLocation",
    "CompanyConfig",
]
