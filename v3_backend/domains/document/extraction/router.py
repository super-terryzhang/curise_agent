"""Extractor dispatch: pick the right backend for a given MIME type.

Usage:

    from domains.document.extraction import extract

    doc = extract(file_bytes, mime_type="application/pdf")

Extractors are registered on module import (side-effect of importing the
extractor submodule). New extractors can register themselves without touching
this file — see `pypdf_extractor.py` / `excel_extractor.py` for examples.
"""

from __future__ import annotations

import logging

from domains.document.extraction.base import ExtractionError, Extractor
from domains.document.extraction.schema import ExtractedDocument

logger = logging.getLogger(__name__)

_EXTRACTORS: list[Extractor] = []


def register_extractor(extractor: Extractor) -> None:
    """Add an extractor to the dispatch chain (first-match wins)."""
    _EXTRACTORS.append(extractor)


def extract(file_bytes: bytes, mime_type: str) -> ExtractedDocument:
    """Dispatch to the first registered extractor that supports the MIME type.

    Raises:
        ExtractionError(kind="no_extractor") if no extractor supports the type.
    """
    _bootstrap()
    for ext in _EXTRACTORS:
        if ext.supports(mime_type):
            logger.info("Dispatching %s → %s", mime_type, ext.name)
            return ext.extract(file_bytes, mime_type)
    raise ExtractionError(
        f"No extractor registered for MIME type '{mime_type}'",
        kind="no_extractor",
    )


def list_extractors() -> list[Extractor]:
    _bootstrap()
    return list(_EXTRACTORS)


_bootstrapped = False


def _bootstrap() -> None:
    global _bootstrapped
    if _bootstrapped:
        return
    # Side-effect imports — each module calls register_extractor() at import
    # time. Order matters: first-match wins, so the lighter direct extractors
    # (pypdfium2 for PDF, openpyxl for .xlsx) win their formats before the
    # heavier markitdown adapter would otherwise claim them. Vision is last
    # because it only claims image/* MIMEs, no overlap with the others.
    from domains.document.extraction import (  # noqa: F401
        excel_extractor,
        markitdown_extractor,
        pypdf_extractor,
        vision_extractor,
    )

    _bootstrapped = True
