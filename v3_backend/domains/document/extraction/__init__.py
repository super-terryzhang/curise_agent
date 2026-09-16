"""Universal document extraction layer.

Public API:
- `ExtractedDocument` — the contract between extraction and downstream
- `Extractor` protocol — `extract(bytes, mime_type) -> ExtractedDocument`
- `ExtractionError` — raised on unrecoverable failures
- `extract(file_bytes, mime_type)` — top-level entry point

Extractors are domain-agnostic: they produce a markdown string + minimal
metadata. Domain interpretation (purchase_order vs invoice vs ...) happens
later in the classifier + projector chain.
"""

from domains.document.extraction.base import ExtractionError, Extractor
from domains.document.extraction.router import (
    extract,
    list_extractors,
    register_extractor,
)
from domains.document.extraction.schema import (
    EXTRACTION_SCHEMA_VERSION,
    ExtractedDocument,
)

__all__ = [
    "EXTRACTION_SCHEMA_VERSION",
    "ExtractedDocument",
    "Extractor",
    "ExtractionError",
    "extract",
    "list_extractors",
    "register_extractor",
]
