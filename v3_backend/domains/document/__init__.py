"""Document domain — the parent type for every uploaded file (ADR-0001).

Public API:
- `service`: upload, list, get, update_doc_type, delete, reextract, build_order_payload
- `schemas`: Pydantic DTOs
- `classifier`: doc_type detection registry
- `projector_registry`: doc_type → subtype projector map
- `extraction`: bytes → ExtractedDocument
- `models`: Document ORM (for Alembic; do NOT import from other domains)
"""

from domains.document import (
    classifier,
    extraction,
    projector_registry,
    schemas,
    service,
)
from domains.document.extraction import ExtractedDocument, extract
from domains.document.models import Document

__all__ = [
    "service",
    "schemas",
    "classifier",
    "projector_registry",
    "extraction",
    "extract",
    "Document",
    "ExtractedDocument",
]
