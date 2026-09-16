"""LLM-backed summarizer — produces tags + 2-3 paragraph summary from a
document's extracted markdown.

Runs after the universal extractor, before the workflow flips the document
to `status="extracted"`. The output powers two consumers:

  1. **Document detail page**: `summary` is rendered as the headline
     abstract above the markdown viewer; `tags` show as filterable chips.
  2. **Agent retrieval**: when the chat agent needs to find relevant
     documents, matching against `tags + summary` is far cheaper than
     scanning full markdown bodies.

Error semantics (intentionally lenient — failure here must NOT poison the
extraction status):

  - Missing GOOGLE_API_KEY    → returns None (workflow skips, no error)
  - Empty / blank markdown    → returns None
  - API call failure          → raises SummarizerError
  - Unparseable / empty reply → raises SummarizerError

Callers in the workflow catch SummarizerError and just log; the document
still finishes as `extracted` without summary/tags. The next re-extract
or backfill run will retry.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypedDict

from infrastructure.config import settings

logger = logging.getLogger(__name__)


class SummarizerError(Exception):
    """Hard failure inside the LLM summarization call."""


class SummarizerResult(TypedDict):
    summary: str
    tags: list[str]
    language: str | None


# Cap on markdown sent to Gemini — keeps token cost predictable + avoids
# Gemini Flash's 32k input limit for absurdly long Excels. The first
# ~12k chars cover every realistic PO/invoice/manifest by a wide margin.
_MAX_MARKDOWN_CHARS = 12_000

# Per-call wall-clock cap; 60s is generous for Flash on a 12k-char input.
_TIMEOUT_MS = 60_000

# Tag count target — the model is asked to return 3-8 tags. Validation
# below clamps anything outside this band.
_TAG_MIN = 3
_TAG_MAX = 8


# Server-side typed contract for Gemini's JSON output.
_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "tags"],
    "properties": {
        "summary": {
            "type": "string",
            "description": (
                "A 2-3 paragraph natural-language summary of the document. "
                "Written in the document's primary language. Plain prose, "
                "no markdown formatting. Each paragraph 2-4 sentences."
            ),
        },
        "tags": {
            "type": "array",
            "minItems": _TAG_MIN,
            "maxItems": _TAG_MAX,
            "items": {
                "type": "string",
                "description": (
                    "Lowercase kebab-case topic tag (e.g. 'beef-supplier', "
                    "'celebrity-cruise', 'frozen-cargo'). Avoid generic "
                    "tags like 'document' or 'file'. 1-3 words per tag."
                ),
            },
        },
        "language": {
            "type": "string",
            "nullable": True,
            "description": (
                "ISO 639-1 language code of the document's primary language "
                "(e.g. 'zh', 'en', 'ja'). Null if mixed or undeterminable."
            ),
        },
    },
}


_PROMPT_TEMPLATE = """\
You are a document analysis assistant for a cruise procurement system.
Given the document below (already extracted to Markdown), produce:

1. **summary**: A 2-3 paragraph natural-language abstract describing what
   this document is about, who it's between (if applicable), the key
   subjects (products, ships, dates, amounts) it covers, and any notable
   structural details (e.g. "this is a label-printing template, not a
   real PO"). Write in the document's primary language. Plain prose; no
   bullet points or markdown.

2. **tags**: 3-8 short topic tags in lowercase kebab-case. Pick tags that
   help future search — be specific:
   - Subject area: `beef-supplier`, `frozen-meat`, `produce`
   - Counterparty / vessel: `celebrity-solstice`, `royal-caribbean`
   - Document nature: `inquiry-form`, `box-label-template`, `quotation`
   - Time/window: `delivery-2026-q2`, `march-2026`
   Avoid generic tags like `document`, `file`, `data`, `pdf`.

3. **language**: ISO 639-1 code of the document's primary language.

## Document context
- Filename: {filename}
- File type: {file_type}
- Document type (system classification): {doc_type}

## Document content (extracted Markdown)
{markdown}
"""


def summarize_document(
    markdown: str,
    *,
    filename: str | None = None,
    file_type: str | None = None,
    doc_type: str | None = None,
) -> SummarizerResult | None:
    """Run Gemini once on the document's markdown and return tags + summary.

    Returns None when summarization is deliberately skipped (no API key,
    empty markdown). Raises SummarizerError on real failures.
    """
    api_key = settings.GOOGLE_API_KEY
    if not api_key:
        logger.info("summarizer: GOOGLE_API_KEY not configured — skipping")
        return None

    text = (markdown or "").strip()
    if not text:
        return None

    truncated = text[:_MAX_MARKDOWN_CHARS]
    if len(text) > _MAX_MARKDOWN_CHARS:
        truncated += "\n\n…(truncated for summarization)"
        logger.info(
            "summarizer: markdown truncated %d → %d chars",
            len(text),
            _MAX_MARKDOWN_CHARS,
        )

    prompt = _PROMPT_TEMPLATE.format(
        filename=filename or "(unknown)",
        file_type=file_type or "(unknown)",
        doc_type=doc_type or "(unknown)",
        markdown=truncated,
    )

    model = settings.AGENT_PO_EXTRACT_MODEL
    raw = _call_gemini(api_key, model, prompt)
    return _parse_response(raw)


# ─── Gemini call ────────────────────────────────────────────


def _call_gemini(api_key: str, model: str, prompt: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key, http_options={"timeout": _TIMEOUT_MS})
    try:
        response = client.models.generate_content(
            model=model,
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
            ),
        )
    except Exception as exc:
        raise SummarizerError(f"Gemini API call failed: {exc}") from exc

    text = getattr(response, "text", None) or ""
    if not text.strip():
        raise SummarizerError("Gemini returned empty response")
    return text


# ─── Response parsing ───────────────────────────────────────


def _parse_response(raw: str) -> SummarizerResult:
    """Parse Gemini JSON, normalize, and validate."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SummarizerError(f"Gemini returned non-JSON: {raw[:200]}") from exc

    summary = (data.get("summary") or "").strip()
    if not summary:
        raise SummarizerError("Gemini response missing 'summary'")

    raw_tags = data.get("tags") or []
    tags = _normalize_tags(raw_tags)
    if not tags:
        raise SummarizerError("Gemini response produced no usable tags")

    language = data.get("language") or None
    if isinstance(language, str):
        language = language.strip().lower() or None

    return SummarizerResult(summary=summary, tags=tags, language=language)


def _normalize_tags(raw: list[Any]) -> list[str]:
    """Squash to lowercase kebab-case, drop dupes/empties, cap to _TAG_MAX."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        tag = item.strip().lower().replace(" ", "-").replace("_", "-")
        # Drop punctuation that isn't useful in a kebab-case tag.
        tag = "".join(c for c in tag if c.isalnum() or c in "-:")
        tag = tag.strip("-:")
        if not tag or tag in seen:
            continue
        seen.add(tag)
        cleaned.append(tag)
        if len(cleaned) >= _TAG_MAX:
            break
    return cleaned
