"""Extractor protocol + error type."""

from __future__ import annotations

from typing import Protocol

from domains.document.extraction.schema import ExtractedDocument


class ExtractionError(Exception):
    """Raised when an extractor cannot produce a valid ExtractedDocument.

    `kind` taxonomy (workflow uses this to decide stored vs error):
      - "no_extractor":  the router has no extractor registered for this
                         MIME — the format is unsupported. Workflow marks
                         the document `status="stored"` silently.
      - "unconfigured":  an extractor exists but cannot run because of a
                         missing runtime config (API key, optional
                         dependency). Workflow marks `stored` AND sets
                         `processing_error` so the user knows why.
      - "input":         file too large, corrupt, or otherwise unusable.
      - "provider":      upstream API error, network failure.
      - "truncated":     output exceeded max_output_tokens.
      - "parse":         provider returned a response we couldn't decode.
      - "empty":         extractor ran but produced no usable content.

    Legacy: `"config"` was historically used for both "no_extractor" and
    "unconfigured". Phase 3.4 split them; new code MUST pick one. The
    workflow still recognises `"config"` and treats it as `"no_extractor"`
    so any third-party extractor not yet migrated keeps working.
    """

    def __init__(self, message: str, kind: str = "provider") -> None:
        super().__init__(message)
        self.kind = kind


class Extractor(Protocol):
    """Pluggable extractor contract."""

    name: str

    def supports(self, mime_type: str) -> bool:
        """True if this extractor can handle the given MIME type."""
        ...

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractedDocument:
        """Convert raw bytes to an ExtractedDocument. Raise ExtractionError on failure."""
        ...
