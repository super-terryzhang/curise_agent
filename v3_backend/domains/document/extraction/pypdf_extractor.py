"""PDF extractor — text-layer first, Gemini multimodal fallback for scans.

Two layers behind one Extractor entry:

1. `PyPDFiumExtractor` (the cheap path) — pure-Python text-layer extraction
   via `pypdfium2`. Handles every born-digital PDF in milliseconds with no
   network or API cost.

2. `PdfOrchestrator` (the registered entry) — wraps PyPDFiumExtractor and
   when text-layer extraction returns empty `blocks` (i.e. the PDF is a
   scan with no embedded text), falls through to Gemini's native PDF
   multimodal endpoint to OCR the pages. The cheap path therefore stays
   free for the 95% case; only true scans pay the Gemini bill.

Boundaries of the fallback:
- Real `pypdfium2` failures (empty bytes, corrupt PDF) propagate verbatim
  — we don't fallback on a parser error, only on "parsed fine, no text".
- Missing `GOOGLE_API_KEY` while the PDF needs OCR raises
  `ExtractionError(kind="unconfigured")`. The workflow stores the file
  with `status="stored"` and surfaces a `processing_error` so the user
  knows to ask the admin to configure the key.
- Successful Gemini call → returns a single ParagraphBlock carrying the
  markdown transcription, with `stats.extractor="gemini-pdf"`.
"""

from __future__ import annotations

import logging
import time
from io import BytesIO
from typing import Any, Callable

from domains.document.extraction.base import ExtractionError
from domains.document.extraction.router import register_extractor
from domains.document.extraction.schema import (
    EXTRACTION_SCHEMA_VERSION,
    ExtractedDocument,
)

logger = logging.getLogger(__name__)

_SUPPORTED = {"application/pdf", "pdf"}


_GEMINI_PDF_PROMPT = (
    "Transcribe everything visible in this PDF as Markdown.\n\n"
    "Rules:\n"
    "- Preserve all visible text verbatim across all pages.\n"
    "- Render tables as Markdown tables.\n"
    "- Render labelled fields (key: value pairs) as a bullet list.\n"
    "- Use `## Page N` headings to delimit pages.\n"
    "- Do NOT add commentary, summaries, or framing text outside the "
    "transcription itself."
)


# Per-page combined OCR + structured extraction prompt.
_GEMINI_PAGE_OCR_AND_STRUCTURE_PROMPT = """Extract this PDF page's content. Return JSON with these fields:

- "markdown": Brief plain-text transcription of headers, tables, and labels visible on this page. Tables as markdown tables. Keep it under 5000 chars — focus on data, skip lengthy legal/instruction paragraphs.
- "products": List of product line items VISIBLE ON THIS PAGE. For each item, fill ONLY these fields:
  - product_code: the product/SKU code (e.g. "99PRD010588")
  - product_name: SHORT product name ONLY (e.g. "APPLE GRANNY SMITH US EXTRA FANCY 125CT/40LB"). Do NOT include "Item Description:", "Maker's Ref:", "Supplier Part Number:" or any other field labels. Just the product name string.
  - quantity, unit, unit_price, total_price: numbers visible in the product row
  Empty list if this page has no product table.
- "po_number", "ship_name", "delivery_date", "order_date", "destination_port", "currency", "total_amount": Order-level metadata IF visible on this page. Use null for fields not on this page.
- "vendor_name": The SUPPLIER (the company being asked to provide goods, "Sold To" / "Supplier" / "Vendor" / "Vendor Name" field), NOT the cruise line ordering the goods. Common cruise-line buyers (Celebrity Cruises, Royal Caribbean, Carnival, etc.) are NOT the vendor. Use null if no clear supplier field.

Be exact. Do not invent values. Use null for any field not visible."""


# JSON schema matching the prompt above. Used as Gemini's response_schema
# so output is guaranteed to be parseable JSON.
def _page_extraction_schema() -> Any:
    """Lazy build the response schema (needs google.genai.types)."""
    from google.genai import types
    return types.Schema(
        type=types.Type.OBJECT,
        properties={
            "markdown": types.Schema(type=types.Type.STRING),
            "products": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "product_code": types.Schema(type=types.Type.STRING, nullable=True),
                        "product_name": types.Schema(type=types.Type.STRING),
                        "quantity": types.Schema(type=types.Type.NUMBER, nullable=True),
                        "unit": types.Schema(type=types.Type.STRING, nullable=True),
                        "unit_price": types.Schema(type=types.Type.NUMBER, nullable=True),
                        "total_price": types.Schema(type=types.Type.NUMBER, nullable=True),
                    },
                    required=["product_name"],
                ),
            ),
            "po_number": types.Schema(type=types.Type.STRING, nullable=True),
            "ship_name": types.Schema(type=types.Type.STRING, nullable=True),
            "vendor_name": types.Schema(type=types.Type.STRING, nullable=True),
            "delivery_date": types.Schema(type=types.Type.STRING, nullable=True),
            "order_date": types.Schema(type=types.Type.STRING, nullable=True),
            "destination_port": types.Schema(type=types.Type.STRING, nullable=True),
            "currency": types.Schema(type=types.Type.STRING, nullable=True),
            "total_amount": types.Schema(type=types.Type.NUMBER, nullable=True),
        },
        required=["markdown", "products"],
    )


# ─── Cheap path: text-layer extraction ──────────────────────


class PyPDFiumExtractor:
    """Pure-Python text-layer extractor.

    Returns a normal ExtractedDocument with one ParagraphBlock per
    paragraph it found. Returns success with empty blocks when the PDF
    has no text layer (scanned PDFs) — the orchestrator above uses that
    as the trigger to call Gemini.
    """

    name = "pypdfium2"

    def supports(self, mime_type: str) -> bool:
        return mime_type in _SUPPORTED or mime_type.startswith("application/pdf")

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractedDocument:
        if not file_bytes:
            raise ExtractionError("empty PDF", kind="empty")
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:  # pragma: no cover — dep pinned in pyproject
            raise ExtractionError(
                "pypdfium2 is not installed; run `pip install -e .`",
                kind="unconfigured",
            ) from exc

        start = time.perf_counter()
        try:
            pdf = pdfium.PdfDocument(BytesIO(file_bytes))
        except Exception as exc:
            raise ExtractionError(f"Failed to parse PDF: {exc}", kind="input") from exc

        try:
            page_count = len(pdf)
            title: str | None = None
            paragraphs: list[str] = []
            for page in pdf:
                textpage = page.get_textpage()
                text = textpage.get_text_bounded() or ""
                textpage.close()
                page.close()

                for para in _split_paragraphs(text):
                    paragraphs.append(para)
                    if title is None and len(para) < 120:
                        title = para

            elapsed = time.perf_counter() - start
            markdown = "\n\n".join(paragraphs)
            doc: ExtractedDocument = {
                "schema_version": EXTRACTION_SCHEMA_VERSION,
                "language": None,
                "page_count": page_count,
                "title": title,
                "markdown": markdown,
                "stats": {
                    "extractor": self.name,
                    "elapsed_seconds": round(elapsed, 3),
                    "input_tokens": None,
                    "output_tokens": None,
                    "finish_reason": None,
                    "truncated": False,
                },
            }
            if not paragraphs:
                logger.info("pypdfium2: no text layer found (likely a scanned PDF)")
            return doc
        finally:
            pdf.close()


def _split_paragraphs(text: str) -> list[str]:
    """Split page text into paragraph candidates — very simple heuristic."""
    lines = [line.strip() for line in text.splitlines()]
    paragraphs: list[str] = []
    buffer: list[str] = []
    for line in lines:
        if not line:
            if buffer:
                paragraphs.append(" ".join(buffer).strip())
                buffer = []
            continue
        buffer.append(line)
    if buffer:
        paragraphs.append(" ".join(buffer).strip())
    return [p for p in paragraphs if p]


# ─── Orchestrator: text-first, Gemini fallback ──────────────


def _default_client_factory(api_key: str) -> Any:
    from google import genai

    # 180s was too lenient — single-shot whole-PDF calls that pre-date the
    # chunked path. Chunked path uses per-page calls capped at ~35s each by
    # max_output_tokens, so a 60s ceiling here cuts off any genuine stuck
    # call without affecting healthy ones.
    return genai.Client(api_key=api_key, http_options={"timeout": 60_000})


def _default_pdf_part(file_bytes: bytes) -> Any:
    from google.genai import types

    return types.Part.from_bytes(data=file_bytes, mime_type="application/pdf")


def _load_api_key() -> str:
    from infrastructure.config import settings

    return settings.GOOGLE_API_KEY or ""


def _load_model() -> str:
    from infrastructure.config import settings

    return settings.AGENT_PO_EXTRACT_MODEL


def _load_ocr_model() -> str:
    """Model used for chunked per-page OCR. Defaults to flash-lite (5x faster
    than flash for image transcription per 2026-05-13 benchmark)."""
    from infrastructure.config import settings

    return getattr(settings, "AGENT_OCR_MODEL", "") or "gemini-2.5-flash-lite"


class PdfOrchestrator:
    """PDF entry point that combines text-layer + Gemini OCR fallback."""

    name = "pdf-orchestrator"

    def __init__(
        self,
        text_extractor: PyPDFiumExtractor | None = None,
        client_factory: Callable[[str], Any] = _default_client_factory,
        pdf_part_factory: Callable[[bytes], Any] = _default_pdf_part,
        api_key_loader: Callable[[], str] | None = None,
        model_loader: Callable[[], str] | None = None,
    ) -> None:
        self._text_extractor = text_extractor or PyPDFiumExtractor()
        self._client_factory = client_factory
        self._pdf_part_factory = pdf_part_factory
        self._api_key_loader = api_key_loader or _load_api_key
        self._model_loader = model_loader or _load_model

    def supports(self, mime_type: str) -> bool:
        return self._text_extractor.supports(mime_type)

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractedDocument:
        # Real failures (empty bytes / corrupt PDF) propagate verbatim — we
        # only fallback on the "parsed OK but no text" case (i.e. scanned PDF).
        text_result = self._text_extractor.extract(file_bytes, mime_type)
        if (text_result.get("markdown") or "").strip():
            return text_result

        api_key = self._api_key_loader()
        if not api_key:
            raise ExtractionError(
                "Scanned PDF needs Gemini OCR fallback, but GOOGLE_API_KEY is not configured",
                kind="unconfigured",
            )

        return self._extract_with_gemini(file_bytes, api_key, text_result)

    def _extract_with_gemini(
        self,
        file_bytes: bytes,
        api_key: str,
        baseline: ExtractedDocument,
    ) -> ExtractedDocument:
        # Path A: whole-PDF single-shot. Fast for small docs (< ~15MB / ~10
        # pages). Falls back to chunked on 504/timeout/large-file errors.
        size_mb = len(file_bytes) / 1024 / 1024
        page_count = int(baseline.get("page_count") or 0)
        if size_mb > 15 or page_count > 10:
            logger.info(
                "pdf-orchestrator: %s (%.1f MB, %d pages) — using chunked OCR",
                "large", size_mb, page_count,
            )
            return self._extract_with_gemini_chunked(file_bytes, api_key, baseline)

        start = time.perf_counter()
        try:
            client = self._client_factory(api_key)
            pdf_part = self._pdf_part_factory(file_bytes)
            response = client.models.generate_content(
                model=self._model_loader(),
                contents=[pdf_part, _GEMINI_PDF_PROMPT],
            )
        except Exception as exc:
            err_str = str(exc)
            # 504/DEADLINE/timeout = Gemini choking on the size. Try chunked.
            if "504" in err_str or "DEADLINE" in err_str or "timeout" in err_str.lower():
                logger.warning(
                    "pdf-orchestrator: single-shot failed (%s), falling back to chunked",
                    err_str[:120],
                )
                return self._extract_with_gemini_chunked(file_bytes, api_key, baseline)
            raise ExtractionError(
                f"Gemini PDF OCR failed: {exc}", kind="provider"
            ) from exc

        text = (getattr(response, "text", None) or "").strip()
        if not text:
            raise ExtractionError(
                "Gemini PDF OCR returned no content", kind="empty"
            )

        elapsed = time.perf_counter() - start
        doc: ExtractedDocument = {
            "schema_version": EXTRACTION_SCHEMA_VERSION,
            "language": None,
            "page_count": baseline.get("page_count"),
            "title": _first_heading(text),
            "markdown": text,
            "stats": {
                "extractor": "gemini-pdf",
                "elapsed_seconds": round(elapsed, 3),
                "input_tokens": None,
                "output_tokens": None,
                "finish_reason": None,
                "truncated": False,
            },
        }
        return doc

    def _extract_with_gemini_chunked(
        self,
        file_bytes: bytes,
        api_key: str,
        baseline: ExtractedDocument,
    ) -> ExtractedDocument:
        """Render each page as PNG and OCR independently. Handles arbitrarily
        large scanned PDFs that exceed Gemini's single-call PDF size/time limit.
        """
        import io as _io
        from concurrent.futures import ThreadPoolExecutor, as_completed

        import pypdfium2 as _pdfium

        start = time.perf_counter()
        try:
            src = _pdfium.PdfDocument(file_bytes)
        except Exception as exc:
            raise ExtractionError(
                f"pypdfium failed to open PDF for chunked OCR: {exc}",
                kind="input",
            ) from exc

        n_pages = len(src)
        if n_pages == 0:
            raise ExtractionError("PDF has 0 pages", kind="empty")

        # Render every page to JPEG bytes once, up front. JPEG @ scale 1.5
        # benchmarked ~50% faster on the wire than PNG @ scale 2.0 (165 KB
        # vs 346 KB per page) with no measurable OCR quality difference.
        page_images: list[bytes] = []
        for i in range(n_pages):
            try:
                pil = src[i].render(scale=1.5).to_pil()
                # JPEG needs RGB mode (no alpha).
                if pil.mode != "RGB":
                    pil = pil.convert("RGB")
                buf = _io.BytesIO()
                pil.save(buf, format="JPEG", quality=85)
                page_images.append(buf.getvalue())
            except Exception as exc:
                logger.warning("pdf-orchestrator: page %d render failed: %s", i + 1, exc)
                page_images.append(b"")

        # OCR each page via Gemini Vision in parallel.
        # Performance tuning (2026-05-13 benchmark on 25 MB / 38-page scanned PO):
        #   - model: gemini-2.5-flash-lite is 5x faster than -flash for OCR
        #     (4.5s vs 35s per page; flash hallucinated 18k chars on page 2 at
        #     scale 2.0, flash-lite stable 5k chars matching v2 baseline).
        #   - max_workers: 8 (was 4). Each Gemini Vision call ~5s, so 8 in
        #     flight = ~40s wall clock for 38 pages. Quota safe (paid tier
        #     handles >100 RPM).
        #   - per-call client-side timeout: 60s. Catches the rare hallucinated-
        #     loop case (flash sometimes generates 100K+ tokens, locks for
        #     30-60s). Hard timeout + retry is cheaper than letting one bad
        #     page hold up the pool.
        ocr_model = _load_ocr_model()
        client = self._client_factory(api_key)

        # Per-page OCR + structured extraction in a SINGLE LLM call (was 2
        # round-trips: OCR → markdown → second LLM call → JSON).
        # response_schema forces valid JSON output.
        # max_output_tokens=16384: complex pages with many product rows can
        # need >8K tokens; 8K caused JSON truncation ("Unterminated string")
        # on 16/38 pages of the 25 MB CCI PDF.
        try:
            from google.genai import types as _types
            _gen_config = _types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_page_extraction_schema(),
                max_output_tokens=16384,
            )
        except Exception:
            _gen_config = None  # SDK old? fall through to default

        def _extract_page(idx_img: tuple[int, bytes]) -> tuple[int, dict[str, Any]]:
            """Returns (page_idx, parsed_json) — or empty dict on failure."""
            import json as _json
            idx, img = idx_img
            if not img:
                return idx, {}
            try:
                from google.genai import types as _types
                part = _types.Part.from_bytes(data=img, mime_type="image/jpeg")
                kwargs = {
                    "model": ocr_model,
                    "contents": [part, _GEMINI_PAGE_OCR_AND_STRUCTURE_PROMPT],
                }
                if _gen_config is not None:
                    kwargs["config"] = _gen_config
                resp = client.models.generate_content(**kwargs)
                raw = (getattr(resp, "text", None) or "").strip()
                if not raw:
                    return idx, {}
                parsed = _json.loads(raw)
                return idx, parsed
            except Exception as exc:
                logger.warning(
                    "pdf-orchestrator: page %d extract failed: %s",
                    idx + 1, str(exc)[:120],
                )
                return idx, {}

        page_results: dict[int, dict[str, Any]] = {}
        max_workers = min(8, n_pages)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_extract_page, (i, p)): i
                for i, p in enumerate(page_images)
            }
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    out_idx, data = fut.result()
                    page_results[out_idx] = data
                except Exception as exc:
                    logger.warning(
                        "pdf-orchestrator: page %d hit error: %s",
                        idx + 1, str(exc)[:120],
                    )
                    page_results[idx] = {}

        # Aggregate: concat markdown, concat products, take metadata from
        # whichever page provided it (usually page 1 / last page).
        ordered_md: list[str] = []
        all_products: list[dict[str, Any]] = []
        metadata: dict[str, Any] = {}
        _META_FIELDS = (
            "po_number", "ship_name", "vendor_name", "delivery_date",
            "order_date", "destination_port", "currency", "total_amount",
        )

        for i in range(n_pages):
            page = page_results.get(i) or {}
            md = (page.get("markdown") or "").strip()
            if md:
                ordered_md.append(f"## Page {i+1}\n\n{md}")
            prods = page.get("products") or []
            if isinstance(prods, list):
                all_products.extend(p for p in prods if isinstance(p, dict))
            for field in _META_FIELDS:
                val = page.get(field)
                if val and field not in metadata:
                    metadata[field] = val

        combined = "\n\n---\n\n".join(ordered_md).strip()
        if not combined and not all_products:
            raise ExtractionError(
                f"Gemini chunked extract returned nothing across {n_pages} pages",
                kind="empty",
            )

        elapsed = time.perf_counter() - start
        success_count = sum(
            1 for d in page_results.values() if (d.get("markdown") or "").strip()
        )
        return {
            "schema_version": EXTRACTION_SCHEMA_VERSION,
            "language": None,
            "page_count": n_pages,
            "title": _first_heading(combined),
            "markdown": combined,
            # Structured output — projector reads these instead of triggering
            # a second LLM call to re-derive products from markdown.
            "products": all_products,
            "metadata": metadata,
            "order_metadata": metadata,  # alias for downstream serializers
            "stats": {
                "extractor": "gemini-pdf-chunked-structured",
                "elapsed_seconds": round(elapsed, 3),
                "input_tokens": None,
                "output_tokens": None,
                "finish_reason": (
                    f"{success_count}/{n_pages} pages ok, "
                    f"{len(all_products)} products extracted"
                ),
                "truncated": success_count < n_pages,
            },
        }


def _first_heading(markdown_text: str) -> str | None:
    for line in markdown_text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or None
    return None


# Register the orchestrator (NOT PyPDFiumExtractor directly) — single entry
# per format, fallback is an internal concern.
register_extractor(PdfOrchestrator())
