"""Background pipeline: extract → classify → enrich.

Each stage is independent and reports its own failure. If extraction fails,
the document ends in status="error" with an explanatory `processing_error`.
If classification is unknown, the document is still saved as "extracted" —
the user can manually set `doc_type` later via `/api/documents/{id}`.

For purchase orders, we run a single Gemini call after generic text
extraction to populate `extracted_data.metadata` + `extracted_data.products`.
After successful PO enrichment, the orders domain automatically continues
through Order creation, matching, arrangement inquiry, and anomaly detection.
Human work is reserved for explicit findings rather than normal progression.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from domains.document import repository
from domains.document.extraction import ExtractionError, extract
from infrastructure.db import session as _session_module
from infrastructure.storage import get_storage

logger = logging.getLogger(__name__)


async def run_document_pipeline(document_id: int) -> None:
    """Entry point used by `apps/jobs/runner`. Offloads sync work to a thread."""
    await asyncio.to_thread(_run_sync, document_id)


def _run_sync(document_id: int) -> None:
    # Look up SessionLocal at call time so tests can monkey-patch it.
    with _session_module.SessionLocal() as db:
        doc = repository.get(db, document_id)
        if doc is None:
            logger.warning("pipeline: document %s not found", document_id)
            return
        try:
            doc.status = "extracting"
            repository.save(db, doc)

            content = get_storage().download(doc.file_url) if doc.file_url else b""
            if not content:
                raise ExtractionError("file is empty or storage key missing", kind="input")

            try:
                extracted = extract(content, _mime_for(doc.file_type))
            except ExtractionError as exc:
                # Two distinct "we can't process this right now" cases land
                # the document in `status="stored"` instead of `error`,
                # because the file itself is fine — we just need either a
                # new parser ("no_extractor") or runtime config
                # ("unconfigured"). The legacy "config" tag is kept as a
                # synonym for "no_extractor" so any not-yet-migrated
                # extractor still routes correctly.
                if exc.kind in ("no_extractor", "config"):
                    logger.info(
                        "pipeline: no extractor for document_id=%s file_type=%s — leaving as stored",
                        document_id,
                        doc.file_type,
                    )
                    doc.status = "stored"
                    doc.processing_error = None
                    doc.extracted_data = None
                    doc.content_markdown = None
                    repository.save(db, doc)
                    return
                if exc.kind == "unconfigured":
                    logger.info(
                        "pipeline: extractor unconfigured for document_id=%s: %s",
                        document_id,
                        exc,
                    )
                    doc.status = "stored"
                    doc.processing_error = f"提取器未配置：{exc}"
                    doc.extracted_data = None
                    doc.content_markdown = None
                    repository.save(db, doc)
                    return
                raise
            # Auto-classify via keyword detectors registered in
            # `domains.orders.classifier_rules`. For our cruise-procurement
            # use case, 90%+ of uploads are PO; auto-classify saves the user
            # one click + one round-trip of waiting (the manual-classification
            # path used to fire enrichment lazily, which left users staring at
            # empty metadata fields wondering if upload had failed).
            # Users can still override via PATCH /api/documents/{id} if the
            # classifier misjudged.
            from domains.document import classifier as _classifier
            doc_type = _classifier.classify(dict(extracted)) or "unknown"

            doc.extracted_data = dict(extracted)
            doc.doc_type = doc_type
            doc.extraction_method = extracted.get("stats", {}).get("extractor")
            doc.content_markdown = _build_markdown(dict(extracted))
            doc.processing_error = None
            doc.extracted_at = datetime.utcnow()
            # Stay in `extracting` until enrichment finishes — Gemini may take
            # 5-15s. Frontends poll on `status in (uploaded, extracting)`,
            # so flipping early would freeze the UI on a half-processed view.
            doc.status = "extracting"
            repository.save(db, doc)

            # Subtype-specific enrichment first fills PO metadata + products.
            # Automatic Order creation begins only after that result has been
            # saved successfully below.
            enrichment_failed = False
            if doc_type == "purchase_order":
                try:
                    # Late import: cross-domain hook through orders' public API.
                    from domains.orders import enrich_purchase_order_document

                    enrich_purchase_order_document(doc)
                except Exception as exc:
                    enrichment_failed = True
                    logger.exception("po-enrichment failed for document_id=%s", document_id)
                    doc.processing_error = f"PO 富化失败: {exc}"

            # Tags + summary — independent Gemini call over the markdown so
            # downstream search (human-facing chips + agent retrieval) has a
            # short abstract per document. Failure here is recorded but
            # does not flip the document to `error` — extraction succeeded.
            _populate_tags_and_summary(doc, document_id)

            # Always advance extraction to a terminal state so the frontend
            # stops polling. A successful PO then continues automatically.
            doc.status = "error" if enrichment_failed else "extracted"
            repository.save(db, doc)

            if doc_type == "purchase_order" and not enrichment_failed:
                # Public cross-domain hook. This call stays inside the existing
                # background worker, so upload HTTP responses remain fast while
                # the PO continues through all eight observable stages.
                from domains.orders import automatic_from_document

                automatic_from_document(document_id)

        except ExtractionError as exc:
            logger.warning(
                "pipeline: extraction failed document_id=%s kind=%s: %s",
                document_id,
                exc.kind,
                exc,
            )
            doc.status = "error"
            doc.processing_error = f"抽取失败: {exc}"
            repository.save(db, doc)
        except Exception as exc:
            logger.exception("pipeline: unexpected failure for document %s", document_id)
            doc.status = "error"
            doc.processing_error = f"处理失败: {exc}"
            repository.save(db, doc)


# ─── Helpers ──────────────────────────────────────────────────

def _mime_for(file_type: str) -> str:
    """Resolve a Document.file_type tag to the IETF mime an extractor expects.

    Defers to the shared `file_types` map. Falls through (returns the
    tag itself) for tags already shaped like a mime ("image/jpeg") or
    legacy values not yet whitelisted.
    """
    from domains.document.file_types import default_content_type

    mime = default_content_type(file_type)
    if mime != "application/octet-stream":
        return mime
    # Tag IS already a mime (e.g. "image/jpeg") — let the extractor see it
    return file_type


def _populate_tags_and_summary(doc: Any, document_id: int) -> None:
    """Build deterministic system tags + run the LLM summarizer.

    System tags always present (cheap, derived from existing fields):
      - `file_type:<value>`
      - `doc_type:<value>`  (defaults to `unknown` since auto-classify is off)
      - `extractor:<value>` from the extraction stats
      - `has_products:true` when PO enrichment populated a non-empty list

    LLM summarizer (`summarizer.summarize_document`) runs on top and adds:
      - 3-8 free-form topic tags (`beef-supplier`, `celebrity-cruise`, …)
      - A 2-3 paragraph natural-language summary
      - Optional `lang:<iso>` tag from the detected primary language

    Failures (missing API key, Gemini error, parse error) leave doc.tags +
    doc.summary populated only with the deterministic system tags so the
    document is still searchable. The next backfill / re-extract retries.
    """
    from domains.document.summarizer import (
        SummarizerError,
        summarize_document,
    )

    system_tags: list[str] = []
    if doc.file_type:
        system_tags.append(f"file_type:{doc.file_type}")
    if doc.doc_type:
        system_tags.append(f"doc_type:{doc.doc_type}")
    if doc.extraction_method:
        system_tags.append(f"extractor:{doc.extraction_method}")

    extracted = doc.extracted_data or {}
    products = extracted.get("products") or []
    if isinstance(products, list) and products:
        system_tags.append("has_products:true")

    markdown = (extracted.get("markdown") or doc.content_markdown or "").strip()
    if not markdown:
        # Nothing for the LLM to chew on — keep system tags only.
        doc.tags = system_tags or None
        return

    try:
        result = summarize_document(
            markdown,
            filename=doc.filename,
            file_type=doc.file_type,
            doc_type=doc.doc_type,
        )
    except SummarizerError as exc:
        logger.warning(
            "summarizer failed for document_id=%s: %s — keeping system tags only",
            document_id,
            exc,
        )
        doc.tags = system_tags or None
        return

    if result is None:
        # Skipped (no API key, etc.) — record system tags so search at least works.
        doc.tags = system_tags or None
        return

    # Merge: system tags first (stable filter keys), then LLM topic tags.
    merged: list[str] = list(system_tags)
    if result.get("language"):
        merged.append(f"lang:{result['language']}")
    for tag in result.get("tags", []):
        if tag and tag not in merged:
            merged.append(tag)

    doc.tags = merged
    doc.summary = result.get("summary") or None


def _build_markdown(doc: dict[str, Any]) -> str:
    """Return the canonical markdown content of an extracted document.

    Every extractor populates `markdown` directly as of Phase 3.1+, so this
    is just a normalising read with the document title prepended when the
    extractor didn't already include one.
    """
    md = (doc.get("markdown") or "").strip()
    if not md:
        return ""
    title = doc.get("title")
    if title and not md.lstrip().startswith("# "):
        return f"# {title}\n\n{md}"
    return md
