"""LLM-based enrichment for purchase order documents — does NOT create an Order.

Bridges the document workflow and the Gemini extractor: given a Document
classified as `purchase_order`, run LLM extraction over the raw bytes and
merge `metadata` + `products` back into `document.extracted_data`.

Why this is separate from `project_purchase_order`:
- v2 flow: upload → extract (incl. LLM) → user reviews → click "confirm
  create" → Order is born. Order creation is user-gated, not automatic.
- v3 must match that behavior: LLM enrichment runs on every PO upload, but
  the Order only materializes when `POST /documents/{id}/create-order`
  fires.

This module has zero coupling to the Order model — it only mutates the
Document. The projector (`project_purchase_order`) consumes the enriched
`extracted_data` later, when the user explicitly triggers Order creation.
"""

from __future__ import annotations

import logging

from domains.document import Document
from domains.orders._llm_extractor import (
    ExtractorError,
    extract_po_structure,
)
from infrastructure.storage import get_storage
from infrastructure.storage.base import StorageError

logger = logging.getLogger(__name__)

_SUPPORTED_TYPES = {"pdf", "excel", "xlsx", "xls"}


def enrich_purchase_order_document(document: Document) -> bool:
    """Populate `document.extracted_data` with LLM-extracted metadata + products.

    Returns True when enrichment ran and produced new fields, False when it
    was deliberately skipped (no API key, unsupported file type, no file URL,
    extracted_data already complete) or failed gracefully.

    Hard failures from the extractor are recorded on `document.processing_error`
    but not re-raised — the document workflow continues to a useful terminal
    state regardless.
    """
    if not document.file_url:
        return False
    if (document.file_type or "").lower() not in _SUPPORTED_TYPES:
        return False

    extracted = dict(document.extracted_data or {})
    if isinstance(extracted.get("products"), list) and extracted["products"]:
        # Already have products — nothing to enrich.
        return False

    try:
        file_bytes = get_storage().download(document.file_url)
    except StorageError as exc:
        logger.warning(
            "po-enrich: file %s unavailable for document %d: %s",
            document.file_url,
            document.id,
            exc,
        )
        return False

    try:
        result = extract_po_structure(file_bytes, document.file_type or "")
    except ExtractorError as exc:
        logger.error("po-enrich: LLM extraction failed for document %d: %s", document.id, exc)
        document.processing_error = f"LLM 抽取失败: {exc}"
        return False

    if result is None:
        return False

    metadata = result.get("order_metadata") or {}
    products = result.get("products") or []
    if not metadata and not products:
        return False

    if metadata:
        # Both keys: `metadata` is what `_extract_po_fields` reads; `order_metadata`
        # is the v2-compat surface for the JSON shape downstream consumers see.
        extracted["metadata"] = metadata
        extracted["order_metadata"] = metadata
    if products:
        extracted["products"] = products
    extracted["extraction_method"] = "gemini_native_pdf"
    document.extracted_data = extracted
    logger.info(
        "po-enrich: document %d enriched — %d product(s), metadata fields: %s",
        document.id,
        len(products),
        list(metadata.keys()) if isinstance(metadata, dict) else [],
    )
    return True
