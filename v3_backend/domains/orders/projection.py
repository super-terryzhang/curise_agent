"""Project a Document (doc_type=purchase_order) into an Order row.

Three strategies, tried in order:
  1. If `document.extracted_data["metadata"]` exists (v2-vintage or already
     LLM-enriched), copy those fields directly.
  2. If products are still missing AND the file is retrievable AND a Gemini
     API key is configured, run a single-shot LLM extraction over the raw
     bytes (`_llm_extractor.extract_po_structure`). The result is merged
     back into `document.extracted_data` so subsequent reads see it.
  3. Apply regex + keyword heuristics on the block text as a final fallback.

After projection, if products are present, the matching pipeline runs
synchronously (`run_matching`). Status flow: pending → extracted (LLM
unavailable / no products) → ready (matched).

The projector is registered with `domains.document.projector_registry` so
the Document workflow calls it automatically once classification resolves.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from domains.document import Document, projector_registry
from domains.orders import repository
from domains.orders._llm_extractor import (
    ExtractorError,
)
from domains.orders._llm_extractor import (
    extract_po_structure as _llm_extract_po,
)
from domains.orders.matching import run_matching
from domains.orders.models import Order
from infrastructure.storage import get_storage
from infrastructure.storage.base import StorageError

logger = logging.getLogger(__name__)


# ─── Field extraction heuristics ──────────────────────────────

_PO_PATTERNS = [
    # Hyphenated PO IDs like "PO-TEST-42" — capture the whole token including "PO"
    re.compile(r"\b(PO[-_/][A-Z0-9][A-Z0-9\-_/]*)", re.IGNORECASE),
    re.compile(r"\bPO\s*(?:number|no\.?|#)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-_/]{2,})", re.IGNORECASE),
    re.compile(
        r"\bpurchase\s+order\s+(?:no\.?|number|#)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-_/]{2,})",
        re.IGNORECASE,
    ),
    re.compile(r"発注\s*番号\s*[:：]?\s*([A-Z0-9][A-Z0-9\-_/]{2,})", re.IGNORECASE),
    re.compile(r"注文\s*番号\s*[:：]?\s*([A-Z0-9][A-Z0-9\-_/]{2,})", re.IGNORECASE),
]
_SHIP_PATTERNS = [
    re.compile(r"\bship\s*name\s*[:#]?\s*([A-Z][A-Z0-9 _\-]+)", re.IGNORECASE),
    re.compile(r"\bvessel\s*[:#]?\s*([A-Z][A-Z0-9 _\-]+)", re.IGNORECASE),
    re.compile(r"船名\s*[:：]?\s*([^\n]+)", re.IGNORECASE),
]
_VENDOR_PATTERNS = [
    re.compile(r"\bvendor\s*[:#]?\s*([^\n]+)", re.IGNORECASE),
    re.compile(r"\bsupplier\s*[:#]?\s*([^\n]+)", re.IGNORECASE),
]
_CURRENCY_RE = re.compile(r"\b(USD|JPY|EUR|GBP|AUD|CAD|NZD|HKD|SGD|KRW|INR|THB|CNY)\b")
_DATE_PATTERNS = [
    re.compile(r"\bdelivery\s*date\s*[:#]?\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})", re.IGNORECASE),
    re.compile(r"交货日期\s*[:：]?\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})", re.IGNORECASE),
    re.compile(r"deliver\s*(?:on|by)\s*[:#]?\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})", re.IGNORECASE),
]


# ─── Entry point ──────────────────────────────────────────────


def project_purchase_order(
    document: Document, db: Session, *, run_match_inline: bool = True
) -> Order:
    """Create or update an Order row from a purchase_order Document.

    Pipeline:
        1. Look up / create the Order row (idempotent on document_id).
        2. If the Document doesn't already carry structured products, try
           LLM enrichment (single Gemini call). Result is merged back into
           `document.extracted_data` so subsequent reads see it.
        3. Promote PO fields from `extracted_data.metadata` + regex fallback.
        4. Run the matching pipeline synchronously when products are known.

    Never deletes Order workflow state (match_results, fulfillment_status, ...).
    """
    existing = repository.get_by_document_id(db, document.id)
    order = existing or Order(
        user_id=document.user_id,
        document_id=document.id,
        filename=document.filename,
        file_url=document.file_url,
        file_type=document.file_type,
        status="extracted",
    )

    # Keep Document linkage in sync (file may have been re-uploaded)
    order.document_id = document.id
    order.filename = document.filename
    order.file_url = document.file_url
    order.file_type = document.file_type

    # Step 1: LLM enrichment if needed — fills `extracted_data.metadata`
    # and `extracted_data.products` from the raw file bytes when the
    # generic text extractor (pypdfium2 / openpyxl) didn't.
    #
    # NOTE (2026-05-13): the chunked OCR path now emits structured
    # `products` + `metadata` in one LLM call per page, so this Step 1
    # is usually a no-op for scanned PDFs that went through chunked OCR.
    # Only triggers for: small PDFs, Excel uploads, edge cases.
    extracted = dict(document.extracted_data or {})
    if not _has_products(extracted):
        enriched = _try_llm_enrich(document)
        if enriched is not None:
            extracted = {**extracted, **enriched}
            document.extracted_data = extracted

    # Step 2: Populate PO-specific columns from extracted data + heuristics.
    fields, evidence = _extract_po_fields(document)
    _apply_fields(order, fields, evidence)

    # Step 3: Materialize products onto the Order
    if isinstance(extracted.get("products"), list):
        # A code identifies a SKU, not an order line. Preserve repeated codes;
        # source-row coverage checks decide whether extraction duplicated a row.
        deduped: list[dict[str, Any]] = []
        for p in extracted["products"]:
            if not isinstance(p, dict):
                continue
            deduped.append(p)
        order.products = deduped
        order.product_count = len(order.products)

    order.extraction_data = extracted  # legacy mirror
    grouping = (order.order_metadata or {}).get("_automatic_grouping")
    order.order_metadata = extracted.get("metadata") or extracted.get("order_metadata")
    if grouping:
        order.order_metadata = {**(order.order_metadata or {}), "_automatic_grouping": grouping}
    order.status = "extracted"
    order.processed_at = datetime.utcnow()

    # Persist everything from steps 1-3 BEFORE matching — matching commits its
    # own state, and we want the projection result to survive a matching
    # failure rather than be rolled back together.
    if existing is None:
        db.add(order)
        db.commit()
        db.refresh(order)
    else:
        repository.save(db, order)

    # Step 4: Auto-run matching when we have products to match — unless
    # the caller opted out. The async-create-order path (P0 fix,
    # 2026-06-21) skips this so the HTTP request returns in < 500ms; the
    # background job runs matching afterwards. Synchronous callers
    # (legacy fallback + `reprocess_order`) still keep the inline match.
    if order.products and run_match_inline:
        try:
            run_matching(order, db)
            order.status = "ready"
            order.processing_error = None
            repository.save(db, order)
        except Exception as exc:
            logger.error(
                "matching failed for order %d (document %d): %s",
                order.id,
                document.id,
                exc,
                exc_info=True,
            )
            order.processing_error = f"匹配失败: {exc}"
            # Keep status="extracted" — partial success, user can retry via /rematch.
            repository.save(db, order)
    from domains.orders.groups.automation import auto_group_order

    auto_group_order(db, order.id)
    db.refresh(order)
    return order


# ─── LLM enrichment ───────────────────────────────────────────


def _has_products(extracted: dict[str, Any]) -> bool:
    """Did upstream extraction already produce a usable product list?"""
    products = extracted.get("products")
    return isinstance(products, list) and len(products) > 0


def _try_llm_enrich(document: Document) -> dict[str, Any] | None:
    """Attempt LLM extraction; return enriched dict to merge, or None.

    Returns None on graceful skip (no API key, file unavailable, file_type
    unsupported). Hard failures from the extractor are logged and recorded
    on `document.processing_error` but do not propagate — the projector
    must always produce a valid Order row.
    """
    if not document.file_url:
        return None
    if (document.file_type or "").lower() not in {"pdf", "excel", "xlsx", "xls"}:
        return None

    try:
        file_bytes = get_storage().download(document.file_url)
    except StorageError as exc:
        logger.warning(
            "po-projector: file %s unavailable for LLM enrich: %s", document.file_url, exc
        )
        return None

    # If OCR already produced markdown (e.g. scanned PDF went through the
    # chunked OCR path), prefer that — cheaper, faster, no risk of re-hitting
    # the same 504 we hit when feeding the raw 25MB PDF to Gemini.
    md_hint: str | None = None
    if isinstance(document.extracted_data, dict):
        md = document.extracted_data.get("markdown")
        if isinstance(md, str) and md.strip():
            md_hint = md

    try:
        result = _llm_extract_po(
            file_bytes, document.file_type or "", markdown_hint=md_hint
        )
    except ExtractorError as exc:
        logger.error("po-projector: LLM extraction failed for document %d: %s", document.id, exc)
        document.processing_error = f"LLM 抽取失败: {exc}"
        return None

    if result is None:
        return None

    metadata = result.get("order_metadata") or {}
    products = result.get("products") or []
    if not metadata and not products:
        return None

    enriched: dict[str, Any] = {}
    if metadata:
        # Store under both keys — `_extract_po_fields` reads `metadata`,
        # downstream consumers / serializers may read `order_metadata`.
        enriched["metadata"] = metadata
        enriched["order_metadata"] = metadata
    if products:
        enriched["products"] = products
    enriched["extraction_method"] = "gemini_native_pdf"
    return enriched


# ─── Strategy 1: v2 metadata JSON ─────────────────────────────


_PO_FIELD_KEYS = (
    "po_number",
    "ship_name",
    "vendor_name",
    "delivery_date",
    "loading_date",
    "order_date",
    "currency",
    "destination_port",
)


def _extract_po_fields(document: Document) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (field_values, field_evidence).

    Tries the v2 JSON blob first, then regex on block text. Whichever
    strategy produces a value gets its name recorded in evidence.
    """
    fields: dict[str, Any] = {}
    evidence: dict[str, Any] = {}

    metadata = (document.extracted_data or {}).get("metadata") or {}
    if isinstance(metadata, dict):
        for key in _PO_FIELD_KEYS:
            value = metadata.get(key)
            if value not in (None, "", []):
                fields[key] = value
                evidence[key] = {"source": "extracted_data.metadata", "value": value}

        total = metadata.get("total_amount")
        if total not in (None, "", []):
            try:
                fields["total_amount"] = float(total)  # type: ignore[arg-type]
                evidence["total_amount"] = {
                    "source": "extracted_data.metadata",
                    "value": total,
                }
            except (TypeError, ValueError):
                pass

    # Fall back to heuristics for anything still missing.
    text = _document_text(document)
    if text:
        _apply_regex(text, fields, evidence, "po_number", _PO_PATTERNS)
        _apply_regex(text, fields, evidence, "ship_name", _SHIP_PATTERNS)
        _apply_regex(text, fields, evidence, "vendor_name", _VENDOR_PATTERNS, truncate=200)
        _apply_regex(
            text, fields, evidence, "delivery_date", _DATE_PATTERNS, transform=_normalize_date
        )
        if "currency" not in fields and (m := _CURRENCY_RE.search(text.upper())):
            fields["currency"] = m.group(1)
            evidence["currency"] = {"source": "blocks.regex", "value": m.group(1)}

    return fields, evidence


def _apply_regex(
    text: str,
    fields: dict[str, Any],
    evidence: dict[str, Any],
    key: str,
    patterns: list[re.Pattern[str]],
    *,
    truncate: int | None = None,
    transform: Any = None,
) -> None:
    """Set `fields[key]` from the first regex match, if `key` not already present."""
    if key in fields:
        return
    hit = _first_match(text, patterns)
    if not hit:
        return
    value = hit.strip()
    if truncate is not None:
        value = value[:truncate]
    if transform is not None:
        value = transform(value)
    fields[key] = value
    evidence[key] = {"source": "blocks.regex", "value": hit}


def _apply_fields(order: Order, fields: dict[str, Any], evidence: dict[str, Any]) -> None:
    """Write extracted fields onto the Order ORM."""
    for key in _PO_FIELD_KEYS:
        if key in fields:
            setattr(order, key, fields[key])
    if "total_amount" in fields:
        order.total_amount = fields["total_amount"]

    if evidence:
        merged = dict(order.field_evidence or {})
        merged.update(evidence)
        order.field_evidence = merged

    # Also mirror onto order_metadata so old consumers keep working.
    md = dict(order.order_metadata or {})
    for key, value in fields.items():
        md[key] = value
    order.order_metadata = md


# ─── Helpers ──────────────────────────────────────────────────


def _document_text(document: Document) -> str:
    """Return the document's canonical markdown for regex fallback scans."""
    return str((document.extracted_data or {}).get("markdown") or "")


def _first_match(text: str, patterns: list[re.Pattern[str]]) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return None


_DATE_INPUT_FORMATS = ("%Y-%m-%d", "%Y/%m/%d")


def _normalize_date(value: str) -> str:
    """Return value as YYYY-MM-DD if parseable, else the raw string."""
    value = value.strip()
    for fmt in _DATE_INPUT_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return value


# Register on module import
projector_registry.register_projector("purchase_order", project_purchase_order)
