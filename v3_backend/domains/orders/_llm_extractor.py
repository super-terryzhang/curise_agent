"""Gemini-backed structured extraction for purchase orders.

Production-grade extraction returning `{order_metadata, products}`. Used by
the PO projector when the document doesn't already carry structured products
(typical for born-digital PDFs and Excel uploads).

Strategy (v2 `order_processor.smart_extract` parity, refined):

1. **Structured schema** — pass `response_schema` to Gemini so the JSON shape
   is enforced server-side. Stronger than `response_mime_type=json` alone:
   typed fields (number vs string), required keys, nested arrays.
2. **Two attempts, keep best** — LLMs occasionally drop 1-3 products. We run
   up to 2 calls and pick the one with more products + more metadata fields.
3. **Number cross-validation + corrective retry** — after extraction, verify
   `quantity * unit_price ≈ total_price` (within 2%). If anomalies are found,
   we call Gemini once more with the warnings appended to the prompt, asking
   it to re-check the offending rows. Keep the result with fewer warnings.

Failure modes:
- Missing `GOOGLE_API_KEY` → returns None (caller falls through gracefully)
- Unsupported `file_type` → returns None
- Network / API failure on every attempt → raises `ExtractorError`
- JSON parse failure → raises `ExtractorError`

The module is intentionally a thin pipeline. Higher-level orchestration
(status-machine, persistence) lives in `projection.py`.
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any

from infrastructure.config import settings

logger = logging.getLogger(__name__)


class ExtractorError(Exception):
    """Hard failure in LLM extraction (network, API, parse). Caller decides recovery."""


_EXCEL_TYPES = ("excel", "xlsx", "xls")

# Capped at 2 — empirically more than enough; v2 measured 99%+ recovery on
# missed products by attempt 2. Going higher costs $$ with diminishing returns.
_MAX_ATTEMPTS = 2

# Tolerance for `quantity * unit_price ≈ total_price` — 2% accounts for FX
# rounding, line-level discounts, etc. Tighter would false-positive too often.
_NUMBER_TOLERANCE_PCT = 2.0

# Cap warning count surfaced into the corrective-retry prompt — keep prompt
# bounded, the LLM gets the gist from the first 5 examples.
_MAX_HINTS_TO_LLM = 5


# Response schema — server-side typed contract. Gemini will refuse to emit
# anything that doesn't validate, eliminating string-typed numbers and
# malformed JSON. `nullable=True` on optional fields lets the model emit null
# for missing data without us having to post-process.
_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["order_metadata", "products"],
    "properties": {
        "order_metadata": {
            "type": "object",
            "properties": {
                "po_number": {"type": "string", "nullable": True},
                "ship_name": {"type": "string", "nullable": True},
                "vendor_name": {"type": "string", "nullable": True},
                "delivery_date": {
                    "type": "string",
                    "nullable": True,
                    "description": "Date supplier delivers to port, YYYY-MM-DD",
                },
                "loading_date": {
                    "type": "string",
                    "nullable": True,
                    "description": (
                        "Date supplies are loaded onto the cruise ship "
                        "(typically equals the ship's port-call date). "
                        "Distinct from delivery_date which is when the "
                        "supplier ships to the port. YYYY-MM-DD."
                    ),
                },
                "order_date": {
                    "type": "string",
                    "nullable": True,
                    "description": "YYYY-MM-DD format",
                },
                "currency": {"type": "string", "nullable": True},
                "destination_port": {"type": "string", "nullable": True},
                "total_amount": {"type": "number", "nullable": True},
            },
        },
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "product_code": {"type": "string", "nullable": True},
                    "product_name": {"type": "string", "nullable": True},
                    "quantity": {"type": "number", "nullable": True},
                    "unit": {"type": "string", "nullable": True},
                    "unit_price": {"type": "number", "nullable": True},
                    "total_price": {"type": "number", "nullable": True},
                },
            },
        },
    },
}


_BASE_PROMPT = (
    "Extract all data from this purchase order document. "
    "Return a JSON object with two keys:\n"
    "1. order_metadata: {po_number, ship_name, vendor_name, "
    "delivery_date (YYYY-MM-DD), loading_date (YYYY-MM-DD), "
    "order_date (YYYY-MM-DD), currency, "
    "destination_port, total_amount (number or null)}\n"
    "2. products: array of {product_code, product_name, quantity (number), "
    "unit, unit_price (number), total_price (number)}\n\n"
    "Rules:\n"
    "- Extract ALL products from ALL pages\n"
    "- Numbers must be numeric type, not strings\n"
    "- Dates must be YYYY-MM-DD format\n"
    "- vendor_name must be a plain string, not an object\n"
    "- loading_date = when the cruise ship loads supplies onboard "
    "(typically the port-call date / 装船日). Look for labels like "
    "'loading date', '配信日', '装船日', '入船日', '配信予定日'. "
    "Distinct from delivery_date (supplier ship-out to port).\n"
    "- Only extract visible information, do not fabricate\n"
    "- If a field is missing, use null"
)


# ─── Public entry ────────────────────────────────────────────


def extract_po_structure(
    file_bytes: bytes,
    file_type: str,
    *,
    markdown_hint: str | None = None,
) -> dict[str, Any] | None:
    """Run Gemini extraction over a PO document.

    Args:
        file_bytes: Raw file bytes. Required.
        file_type: 'pdf' / 'xlsx' / etc.
        markdown_hint: If provided, the LLM works off this text instead of
            re-processing the raw PDF. Use when OCR already produced markdown
            — avoids Gemini choking on large PDFs (the same 504 we hit on
            25 MB PDFs in the chunked OCR path).

    Returns:
        `{order_metadata, products}` on success.
        `None` when extraction was deliberately skipped (no API key, unsupported
        file type, or empty input).

    Raises:
        `ExtractorError` if every attempt failed.
    """
    if not file_bytes:
        return None

    api_key = settings.GOOGLE_API_KEY
    if not api_key:
        logger.info("po-extractor: GOOGLE_API_KEY not set, skipping LLM extraction")
        return None

    model = settings.AGENT_PO_EXTRACT_MODEL
    file_type_lower = (file_type or "").lower()

    # Fast path: structured extraction over already-OCR'd markdown. Triggered
    # when chunked OCR pre-filled `extracted_data["markdown"]` (large/scanned
    # PDFs). 1000x cheaper + faster than re-feeding raw PDF bytes to Gemini.
    if (markdown_hint or "").strip() and file_type_lower == "pdf":

        def _call(hint_warnings: list[str] | None) -> dict[str, Any]:
            return _extract_from_markdown(
                markdown_hint or "", api_key, model, hint_warnings
            )

    elif file_type_lower == "pdf":
        # v46+: text-first path. Vision-only on multi-page PDFs is stochastic
        # and prone to cross-page column misalignment (prod 2026-05-21 order
        # #104: row 26 product_name went empty, MANGO dropped, names shifted
        # up by one row — reproduced 5/5 runs with Vision, 0/3 with the text
        # path). For text-bearing PDFs we extract via pypdfium2 first and let
        # Gemini work on the layout-preserving text; scanned PDFs (1 char/
        # page measured on a 38-page image PO) auto-fall back to Vision.
        embedded_text, chars_per_page = _extract_pdf_text_via_pypdfium2(file_bytes)
        # Threshold derived from benchmark: prod order #102 (scanned) = 1
        # char/page; all text-based PDFs we tested were ≥ 200 chars/page.
        # 50 is a wide safety margin.
        _TEXT_PATH_MIN_CHARS_PER_PAGE = 50
        # Track which path served the request so prod monitoring can see
        # which PDFs hit each branch — also persisted via extracted_data
        # for postmortem traceability.
        path_used = {"name": "vision", "chars_per_page": chars_per_page}

        if embedded_text and chars_per_page >= _TEXT_PATH_MIN_CHARS_PER_PAGE:
            path_used["name"] = "text"

            def _call(hint_warnings: list[str] | None) -> dict[str, Any]:
                return _extract_from_markdown(
                    embedded_text, api_key, model, hint_warnings
                )
        else:
            logger.info(
                "po-extractor: PDF has %d chars/page (< %d threshold), "
                "treating as scanned and using Vision path",
                chars_per_page, _TEXT_PATH_MIN_CHARS_PER_PAGE,
            )

            def _call(hint_warnings: list[str] | None) -> dict[str, Any]:
                return _extract_pdf(file_bytes, api_key, model, hint_warnings)

    elif file_type_lower in _EXCEL_TYPES:

        def _call(hint_warnings: list[str] | None) -> dict[str, Any]:
            return _extract_excel(file_bytes, api_key, model, hint_warnings)

    else:
        logger.info("po-extractor: file_type=%s not supported, skipping", file_type_lower)
        return None

    # ── Strategy B: up to N attempts, keep best result ──
    best, best_score, last_error = _run_with_attempts(_call)
    if best is None:
        # Every attempt blew up. Surface the last error.
        assert last_error is not None
        raise ExtractorError(f"all {_MAX_ATTEMPTS} extraction attempts failed: {last_error}")

    logger.info(
        "po-extractor: best of %d attempts — %d products, metadata_score=%d",
        _MAX_ATTEMPTS,
        best_score[0],
        best_score[1],
    )

    # ── v46+: text-path quality gate ──
    # If we routed through the text path but came back with > 30% rows
    # missing product_name, the PDF likely has a layout pypdfium2 can't
    # read cleanly (multi-column, weird fonts, etc.). Fall back to Vision
    # for that PDF — Vision is stochastic but at least sees the visual
    # layout. Threshold (30%) is generous: our prod text-path runs see
    # 0 empties on healthy PDFs.
    if (
        file_type_lower == "pdf"
        and not (markdown_hint or "").strip()
        and locals().get("path_used", {}).get("name") == "text"
    ):
        products = best.get("products") or []
        if products:
            empty_count = sum(
                1 for p in products
                if not (p.get("product_name") or "").strip()
            )
            empty_rate = empty_count / len(products)
            if empty_rate > 0.30:
                logger.warning(
                    "po-extractor: text path produced %d/%d empty product_name "
                    "(%.0f%%) — falling back to Vision",
                    empty_count, len(products), empty_rate * 100,
                )
                # Rebuild _call to target Vision and re-run.
                def _vision_call(hint_warnings: list[str] | None) -> dict[str, Any]:
                    return _extract_pdf(file_bytes, api_key, model, hint_warnings)
                vision_best, _vsc, _verr = _run_with_attempts(_vision_call)
                if vision_best is not None:
                    best = vision_best
                    path_used["name"] = "vision_fallback"
                    logger.info("po-extractor: fallback Vision succeeded")

    # ── Strategy C: number cross-validation + corrective retry ──
    warnings = _validate_extraction_numbers(best.get("products") or [])
    if warnings:
        logger.info(
            "po-extractor: %d number warning(s), attempting corrective extraction: %s",
            len(warnings),
            warnings[:_MAX_HINTS_TO_LLM],
        )
        try:
            corrected = _call(warnings)
        except ExtractorError as exc:
            # Corrective attempt failed — keep the original best result.
            logger.warning("po-extractor: corrective attempt failed (%s), keeping best", exc)
        else:
            corrected_warnings = _validate_extraction_numbers(corrected.get("products") or [])
            if len(corrected_warnings) < len(warnings):
                logger.info(
                    "po-extractor: corrective pass reduced warnings %d → %d",
                    len(warnings),
                    len(corrected_warnings),
                )
                best = corrected
            else:
                logger.info(
                    "po-extractor: corrective pass did not improve (%d → %d), keeping original",
                    len(warnings),
                    len(corrected_warnings),
                )

    return best


# ─── Multi-attempt orchestration ─────────────────────────────


def _run_with_attempts(
    call: Any,  # Callable[[list[str] | None], dict]
) -> tuple[dict[str, Any] | None, tuple[int, int], str | None]:
    """Run up to `_MAX_ATTEMPTS` Gemini calls; return best result + score.

    Returns `(best, (product_count, metadata_score), last_error)`. If every
    attempt raised, `best` is None and `last_error` carries the final message.
    """
    best: dict[str, Any] | None = None
    best_score: tuple[int, int] = (-1, -1)
    last_error: str | None = None

    for attempt in range(_MAX_ATTEMPTS):
        try:
            result = call(None)
        except ExtractorError as exc:
            last_error = str(exc)
            logger.warning(
                "po-extractor: attempt %d/%d failed: %s",
                attempt + 1,
                _MAX_ATTEMPTS,
                exc,
            )
            continue
        score = _score_result(result)
        if score > best_score:
            best = result
            best_score = score

    return best, best_score, last_error


def _score_result(result: dict[str, Any]) -> tuple[int, int]:
    """Score an extraction result for "best of N" comparison.

    Tuple ordering ((products, metadata_completeness)) means we compare on
    product count first, then break ties by how many metadata fields were
    extracted. Higher is better. This catches both "missed half the products"
    and "got products but not the PO number".
    """
    products = result.get("products") or []
    metadata = result.get("order_metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    metadata_filled = sum(1 for v in metadata.values() if v not in (None, "", [], {}))
    return (len(products), metadata_filled)


# ─── Number cross-validation ─────────────────────────────────


def _validate_extraction_numbers(products: list[dict[str, Any]]) -> list[str]:
    """Cross-check arithmetic + sanity. Returns warning strings (empty = OK).

    Three checks:
    1. Quantity must be > 0 (a 0 or negative quantity is almost always wrong)
    2. Unit price must be >= 0
    3. quantity * unit_price ≈ total_price (within `_NUMBER_TOLERANCE_PCT`)

    The third one catches the most common LLM mistake: copying a digit wrong
    in a long table. If we see this, the corrective retry usually fixes it
    because the LLM is forced to re-read those rows.
    """
    warnings: list[str] = []

    for i, product in enumerate(products):
        if not isinstance(product, dict):
            continue
        code = product.get("product_code") or f"#{i}"
        qty = product.get("quantity")
        price = product.get("unit_price")
        total = product.get("total_price")

        if qty is not None and isinstance(qty, (int, float)) and qty <= 0:
            warnings.append(f"{code}: quantity={qty} (must be > 0)")

        if price is not None and isinstance(price, (int, float)) and price < 0:
            warnings.append(f"{code}: unit_price={price} (must be >= 0)")

        if (
            isinstance(qty, (int, float))
            and isinstance(price, (int, float))
            and isinstance(total, (int, float))
            and total > 0
            and qty > 0
        ):
            expected = qty * price
            try:
                diff_pct = abs(expected - total) / total * 100
            except ZeroDivisionError:
                continue
            if diff_pct > _NUMBER_TOLERANCE_PCT:
                warnings.append(
                    f"{code}: qty*price={expected:.2f} vs total={total:.2f} ({diff_pct:.1f}% off)"
                )

    return warnings


def _build_corrective_prompt(warnings: list[str]) -> str:
    """Compose the warning hints into a system prompt addendum for retry."""
    capped = warnings[:_MAX_HINTS_TO_LLM]
    bullet = "\n".join(f"  - {w}" for w in capped)
    overflow = ""
    if len(warnings) > _MAX_HINTS_TO_LLM:
        overflow = f"\n  (and {len(warnings) - _MAX_HINTS_TO_LLM} more)"
    return (
        "\n\n## IMPORTANT — Previous extraction had numeric inconsistencies\n"
        "When re-extracting, please double-check these specific issues:\n"
        f"{bullet}{overflow}\n"
        "Re-read the source document carefully for these rows."
    )


# ─── Markdown path ───────────────────────────────────────────


# Cap markdown sent to Gemini for structure extraction.
# Empirically (2026-05-13 benchmark on 38-page scanned PO):
#   80K  → 36/105 products  (too low — drops content)
#   300K → 504 timeout      (too high — Gemini server choke)
#   150K → tested OK with ~120s latency (sweet spot)
# Context: Gemini Flash 1M context isn't the issue; structured JSON output
# generation over very long input slows down geometrically.
_MARKDOWN_INPUT_CAP = 150_000


def _extract_pdf_text_via_pypdfium2(file_bytes: bytes) -> tuple[str, int]:
    """Extract layout-preserving text from a PDF using pypdfium2.

    Returns `(text, chars_per_page)`. On any pypdfium2 failure (corrupt PDF,
    encrypted, etc.) returns `("", 0)` so the caller can fall back to Vision.

    `chars_per_page` is the routing signal: scanned (image-only) PDFs come
    back at ~1 char/page (just page-break whitespace); text-bearing PDFs
    measure in the hundreds. Empirical (2026-05-21 prod sample, n=5):
      - order 102 (38-page scanned cruise PO): 1 char/page → Vision path
      - order 91/95/98/103/104 (text-based): 200-2000 chars/page → text path
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        logger.warning("pypdfium2 not installed; falling back to Vision path")
        return "", 0
    try:
        pdf = pdfium.PdfDocument(file_bytes)
        parts = [page.get_textpage().get_text_bounded() for page in pdf]
    except Exception as exc:
        # Any failure → return empty so we fall back to Vision. We don't
        # raise because pypdfium2 errors on a small fraction of PDFs is
        # expected and recoverable.
        logger.warning("pypdfium2 text extract failed (%s); falling back", exc)
        return "", 0
    text = "\n".join(parts)
    page_count = max(1, len(parts))
    chars_per_page = len(text) // page_count
    return text, chars_per_page


def _extract_from_markdown(
    markdown: str,
    api_key: str,
    model: str,
    hint_warnings: list[str] | None,
) -> dict[str, Any]:
    """Structure extraction over already-OCR'd markdown text.

    Cheaper / faster than re-processing the PDF: just text in, JSON out.
    Used by projector when `document.extracted_data["markdown"]` is filled
    (= chunked OCR path produced text already).
    """
    from google import genai
    from google.genai import types

    text = (markdown or "").strip()
    if not text:
        raise ExtractorError("markdown input is empty")
    if len(text) > _MARKDOWN_INPUT_CAP:
        text = text[:_MARKDOWN_INPUT_CAP] + "\n\n[... truncated ...]"

    # 180s timeout: structured JSON output over 150K chars can take 100-150s
    # for a 36+ product PO. We can't shorten the markdown more without losing
    # products. Cloud Run timeout (540s) still leaves margin for retries.
    client = genai.Client(api_key=api_key, http_options={"timeout": 180_000})

    prompt = (
        _BASE_PROMPT
        + "\n\nINPUT (markdown OCR output):\n\n"
        + text
    )
    if hint_warnings:
        prompt += _build_corrective_prompt(hint_warnings)

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
        raise ExtractorError(f"Gemini markdown extract failed: {exc}") from exc

    return _parse_response(response.text)


# ─── PDF path ────────────────────────────────────────────────


def _extract_pdf(
    file_bytes: bytes,
    api_key: str,
    model: str,
    hint_warnings: list[str] | None,
) -> dict[str, Any]:
    """Native PDF input — Gemini reads the file directly, no OCR pre-step."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key, http_options={"timeout": 180_000})
    pdf_part = types.Part.from_bytes(data=file_bytes, mime_type="application/pdf")

    prompt = _BASE_PROMPT
    if hint_warnings:
        prompt += _build_corrective_prompt(hint_warnings)

    try:
        response = client.models.generate_content(
            model=model,
            contents=[pdf_part, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
            ),
        )
    except Exception as exc:
        raise ExtractorError(f"Gemini API call failed: {exc}") from exc

    return _parse_response(response.text)


# ─── Excel path ──────────────────────────────────────────────


def _extract_excel(
    file_bytes: bytes,
    api_key: str,
    model: str,
    hint_warnings: list[str] | None,
) -> dict[str, Any]:
    """Read all sheets as TSV-ish text, then send to Gemini."""
    from google import genai
    from google.genai import types
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as exc:
        raise ExtractorError(f"Excel parse failed: {exc}") from exc

    text = _excel_to_text(workbook)
    if not text.strip():
        return {"order_metadata": {}, "products": []}

    populated = [line for line in text.splitlines() if line and not line.startswith("===")]
    if not populated:
        return {"order_metadata": {}, "products": []}

    client = genai.Client(api_key=api_key, http_options={"timeout": 180_000})
    prompt = _BASE_PROMPT
    if hint_warnings:
        prompt += _build_corrective_prompt(hint_warnings)
    prompt = f"{prompt}\n\n=== Document Content ===\n{text}"

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
        raise ExtractorError(f"Gemini API call failed: {exc}") from exc

    return _parse_response(response.text)


def _excel_to_text(workbook: Any) -> str:
    """Flatten a workbook into TSV-ish text. Drops trailing empty rows per sheet."""
    chunks: list[str] = []
    for sheet in workbook.worksheets:
        chunks.append(f"=== Sheet: {sheet.title} ===")
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if c is None else str(c) for c in row]
            if any(cell.strip() for cell in cells):
                chunks.append("\t".join(cells))
    return "\n".join(chunks)


# ─── Response parsing ────────────────────────────────────────


def _parse_response(raw: str | None) -> dict[str, Any]:
    """Parse Gemini's JSON response, normalize shape.

    Even with `response_schema`, defensive parsing remains: schema validation
    is server-side but the SDK still returns text we have to json.loads.
    Empty/malformed responses get coerced to ExtractorError so the caller
    can decide between corrective retry and propagation.
    """
    if not raw:
        raise ExtractorError("Gemini returned empty response")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractorError(f"Gemini returned non-JSON: {raw[:200]}") from exc

    metadata = data.get("order_metadata") or {}
    if isinstance(metadata, list):
        metadata = metadata[0] if metadata else {}
    if not isinstance(metadata, dict):
        metadata = {}

    products_raw = data.get("products") or []
    products: list[dict[str, Any]] = (
        [p for p in products_raw if isinstance(p, dict)] if isinstance(products_raw, list) else []
    )

    return {"order_metadata": metadata, "products": products}
