"""Adapter over Microsoft `markitdown` for the long-tail document formats.

Why this lives next to the existing `pypdf_extractor` / `excel_extractor`:
- pypdfium2 stays the PDF path — lighter than markitdown's pdfminer chain.
- openpyxl stays the .xlsx path — avoids pulling in pandas at request time.
- markitdown owns Word (.docx via mammoth), PowerPoint (.pptx),
  legacy Excel (.xls via xlrd), HTML, and the plain-text family
  (csv / md / json / xml / txt).

Output shape is a single ParagraphBlock carrying the full markdown string.
That deliberately keeps the existing `blocks`-based ExtractedDocument
schema unchanged for now — downstream consumers (workflow,
classifier, projection) keep working without any edits in this phase.
A future iteration can promote `markdown` to a first-class field on
the schema.

Markitdown quirks we explicitly handle:
- `convert_stream` requires a `file_extension` hint; we map our short
  file_type tag → extension via `_FILE_TYPE_TO_EXT`.
- For garbage bytes labelled with a structured extension (e.g. random
  bytes labelled `.docx`), markitdown falls through to the plain-text
  converter rather than raising — the resulting "extracted" document
  is whatever decodable text was inside. We accept that behavior; the
  alternative (sniffing magic bytes ourselves) duplicates what
  markitdown's own router already attempts.
"""

from __future__ import annotations

import logging
import time
from io import BytesIO

from domains.document.extraction.base import ExtractionError
from domains.document.extraction.router import register_extractor
from domains.document.extraction.schema import (
    EXTRACTION_SCHEMA_VERSION,
    ExtractedDocument,
)

logger = logging.getLogger(__name__)


# MIME types this extractor claims. PDF (application/pdf) and modern
# Excel (…spreadsheetml.sheet) are intentionally excluded — those are
# served by lighter direct extractors that ship without pandas/pdfminer.
_SUPPORTED_MIMES: frozenset[str] = frozenset(
    {
        # Word
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "word",
        # PowerPoint
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "powerpoint",
        # Legacy Excel binary
        "application/vnd.ms-excel",
        "xls",
        # HTML
        "text/html",
        # Plain text family
        "text/plain",
        "text",
        "text/csv",
        "csv",
        "text/markdown",
        "markdown",
        "application/json",
        "json",
        "application/xml",
        "text/xml",
        "xml",
    }
)


# Map our short file_type tag (or canonical mime) → extension hint that
# markitdown's stream router uses to dispatch to the right converter.
_MIME_TO_EXT: dict[str, str] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "word": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "powerpoint": ".pptx",
    "application/vnd.ms-excel": ".xls",
    "xls": ".xls",
    "text/html": ".html",
    "text/plain": ".txt",
    "text": ".txt",
    "text/csv": ".csv",
    "csv": ".csv",
    "text/markdown": ".md",
    "markdown": ".md",
    "application/json": ".json",
    "json": ".json",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "xml": ".xml",
}


class MarkItDownExtractor:
    name = "markitdown"

    def supports(self, mime_type: str) -> bool:
        return mime_type in _SUPPORTED_MIMES

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractedDocument:
        if not file_bytes:
            raise ExtractionError("empty document", kind="empty")

        try:
            from markitdown import (
                FileConversionException,
                MarkItDown,
                MissingDependencyException,
                UnsupportedFormatException,
            )
        except ImportError as exc:  # pragma: no cover — pinned in pyproject
            raise ExtractionError(
                "markitdown is not installed; run `pip install -e .`",
                kind="unconfigured",
            ) from exc

        ext = _MIME_TO_EXT.get(mime_type)
        if ext is None:
            # supports() should have prevented this; defensive guard so a
            # caller bypassing the router gets a clear error.
            raise ExtractionError(
                f"markitdown extractor refuses MIME '{mime_type}' (no extension hint)",
                kind="no_extractor",
            )

        start = time.perf_counter()
        try:
            md = MarkItDown()
            result = md.convert_stream(BytesIO(file_bytes), file_extension=ext)
        except UnsupportedFormatException as exc:
            raise ExtractionError(
                f"markitdown has no converter for {ext}: {exc}", kind="no_extractor"
            ) from exc
        except MissingDependencyException as exc:
            raise ExtractionError(
                f"markitdown extras missing for {ext}: {exc}", kind="unconfigured"
            ) from exc
        except FileConversionException as exc:
            # All registered converters tried and failed (corrupt file,
            # wrong format, etc.). Treat as bad input.
            raise ExtractionError(
                f"markitdown failed to convert {ext}: {exc}", kind="input"
            ) from exc
        except Exception as exc:  # pragma: no cover — defensive
            raise ExtractionError(
                f"markitdown unexpected failure on {ext}: {exc}", kind="provider"
            ) from exc

        text = (result.text_content or "").strip()
        if not text:
            raise ExtractionError(
                f"markitdown returned no content for {ext}", kind="empty"
            )

        elapsed = time.perf_counter() - start
        title = result.title.strip() if getattr(result, "title", None) else None
        doc: ExtractedDocument = {
            "schema_version": EXTRACTION_SCHEMA_VERSION,
            "language": None,
            "page_count": None,
            "title": title or _first_heading(text),
            "markdown": text,
            "stats": {
                "extractor": self.name,
                "elapsed_seconds": round(elapsed, 3),
                "input_tokens": None,
                "output_tokens": None,
                "finish_reason": None,
                "truncated": False,
            },
        }
        return doc


def _first_heading(markdown_text: str) -> str | None:
    """Surface the first `# heading` line as a document title, if any."""
    for line in markdown_text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or None
    return None


register_extractor(MarkItDownExtractor())
