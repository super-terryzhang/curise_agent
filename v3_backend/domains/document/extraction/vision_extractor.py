"""Vision extractor — Gemini multimodal turns image bytes into a markdown
description with all visible text, tables, fields, and structure.

Owns:
  image/jpeg, image/png, image/gif, image/webp, image/bmp, image/tiff,
  image/heic

We trust Gemini's native multimodal support for these MIMEs (per
ai.google.dev/gemini-api/docs/vision). HEIC is intentionally passed through
without local conversion — adding pillow-heif would require the libheif
system library at deploy time and isn't worth it until we hit a real
failure. If Gemini rejects a specific image MIME we'll see kind="provider"
and can revisit.

Failure modes are mapped to the existing `ExtractionError.kind` taxonomy
so the workflow's status-machine knows how to handle them:
  - Missing GOOGLE_API_KEY  → kind="unconfigured"
  - Empty / 0-byte input    → kind="empty"
  - SDK / API call failure  → kind="provider"
  - Empty response from API → kind="empty"

Testability: the Gemini client and its `Part` helper are pulled through
factory callables so unit tests can inject fakes without monkey-patching
the `google.genai` package globally.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from domains.document.extraction.base import ExtractionError
from domains.document.extraction.router import register_extractor
from domains.document.extraction.schema import (
    EXTRACTION_SCHEMA_VERSION,
    ExtractedDocument,
)

logger = logging.getLogger(__name__)


_SUPPORTED_MIMES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/bmp",
        "image/tiff",
        "image/heic",
    }
)


_PROMPT = (
    "Transcribe everything visible in this image as Markdown.\n\n"
    "Rules:\n"
    "- Preserve all visible text verbatim (do not paraphrase).\n"
    "- Render tables as Markdown tables.\n"
    "- Render labelled fields (key: value pairs) as a bullet list.\n"
    "- If the image contains no readable text, briefly describe what's "
    "  shown and return that description as a single paragraph.\n"
    "- Do NOT add commentary, headings, or framing text outside the "
    "  transcription itself."
)


# Default factories — production paths use these. Tests pass alternates.
def _default_client_factory(api_key: str) -> Any:
    from google import genai  # local import keeps module importable without SDK

    return genai.Client(api_key=api_key, http_options={"timeout": 120_000})


def _default_image_part(image_bytes: bytes, mime_type: str) -> Any:
    from google.genai import types

    return types.Part.from_bytes(data=image_bytes, mime_type=mime_type)


class GeminiVisionExtractor:
    """Image-to-markdown via Gemini multimodal.

    Construction takes optional factories so tests can inject fakes
    without touching the real SDK. Production code instantiates with
    defaults, registered once at module load time.
    """

    name = "gemini-vision"

    def __init__(
        self,
        client_factory: Callable[[str], Any] = _default_client_factory,
        image_part_factory: Callable[[bytes, str], Any] = _default_image_part,
        api_key_loader: Callable[[], str] | None = None,
        model_loader: Callable[[], str] | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._image_part_factory = image_part_factory
        self._api_key_loader = api_key_loader or _load_api_key
        self._model_loader = model_loader or _load_model

    def supports(self, mime_type: str) -> bool:
        return mime_type in _SUPPORTED_MIMES

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractedDocument:
        if not file_bytes:
            raise ExtractionError("empty image", kind="empty")
        if mime_type not in _SUPPORTED_MIMES:
            # Defensive: router should have filtered this out via supports().
            raise ExtractionError(
                f"vision extractor refuses MIME '{mime_type}'", kind="no_extractor"
            )

        api_key = self._api_key_loader()
        if not api_key:
            raise ExtractionError(
                "GOOGLE_API_KEY not configured — cannot run vision extraction",
                kind="unconfigured",
            )

        model = self._model_loader()

        start = time.perf_counter()
        try:
            client = self._client_factory(api_key)
            image_part = self._image_part_factory(file_bytes, mime_type)
            response = client.models.generate_content(
                model=model,
                contents=[image_part, _PROMPT],
            )
        except Exception as exc:
            raise ExtractionError(
                f"Gemini vision call failed: {exc}", kind="provider"
            ) from exc

        text = (getattr(response, "text", None) or "").strip()
        if not text:
            raise ExtractionError("Gemini vision returned no content", kind="empty")

        elapsed = time.perf_counter() - start
        doc: ExtractedDocument = {
            "schema_version": EXTRACTION_SCHEMA_VERSION,
            "language": None,
            "page_count": None,
            "title": _first_heading(text),
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


def _load_api_key() -> str:
    from infrastructure.config import settings

    return settings.GOOGLE_API_KEY or ""


def _load_model() -> str:
    from infrastructure.config import settings

    # Reuse the PO-extract model — same Gemini 2.5 Flash, multimodal-capable.
    # If we ever want a different model just for vision, add a dedicated
    # AGENT_VISION_MODEL setting.
    return settings.AGENT_PO_EXTRACT_MODEL


def _first_heading(markdown_text: str) -> str | None:
    for line in markdown_text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or None
    return None


register_extractor(GeminiVisionExtractor())
