"""Universal extraction schema — the contract between extractor and downstream.

Schema version 2.0 (2026-05-01) — `markdown: str` is the canonical content
representation. The pre-2.0 `blocks: list[Block]` shape was retired in
Phase 3.3 because every consumer (workflow, classifier, projection) only
ever needed flat searchable text and the block-by-block render was an
expensive intermediate that nothing else used.

Bump `EXTRACTION_SCHEMA_VERSION` on breaking changes. Adding optional
fields is non-breaking.
"""

from __future__ import annotations

from typing import TypedDict

EXTRACTION_SCHEMA_VERSION = "2.0"


class ExtractionStats(TypedDict, total=False):
    extractor: str
    elapsed_seconds: float
    input_tokens: int | None
    output_tokens: int | None
    finish_reason: str | None
    truncated: bool


class ExtractedDocument(TypedDict, total=False):
    schema_version: str
    language: str | None
    page_count: int | None
    title: str | None
    # Canonical content as Markdown — the only content field readers should
    # touch. Empty string means "no extractable content" (e.g. an image
    # whose vision call returned nothing).
    markdown: str
    stats: ExtractionStats
