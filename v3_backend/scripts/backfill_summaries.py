#!/usr/bin/env python3
"""Backfill `tags` + `summary` for documents extracted before 2026-05-08.

The summarizer step was added to the workflow on 2026-05-08; existing
documents at that point have `status="extracted"` but no `tags` or
`summary`. This script walks them and fills both.

Behaviour:
- Idempotent — skips any document that already has BOTH `tags` and
  `summary` populated.
- Re-runnable — partial success is fine; a second run picks up only the
  rows still missing the fields.
- Lenient — per-document failures are logged and counted; one bad doc
  does not abort the whole batch.

Usage:

    .venv/bin/python scripts/backfill_summaries.py
    .venv/bin/python scripts/backfill_summaries.py --dry-run
    .venv/bin/python scripts/backfill_summaries.py --only-id 3

Env requirements:
- GOOGLE_API_KEY must be set (otherwise summarizer returns None and
  every doc ends with system-tags-only — still useful, just no LLM
  summary or content tags).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make the v3_backend root importable when invoked from anywhere.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / ".env")

from domains.document.models import Document  # noqa: E402
from domains.document.summarizer import (  # noqa: E402
    SummarizerError,
    summarize_document,
)
from infrastructure.db.session import SessionLocal  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("backfill_summaries")


def _build_system_tags(doc: Document) -> list[str]:
    tags: list[str] = []
    if doc.file_type:
        tags.append(f"file_type:{doc.file_type}")
    if doc.doc_type:
        tags.append(f"doc_type:{doc.doc_type}")
    if doc.extraction_method:
        tags.append(f"extractor:{doc.extraction_method}")
    products = (doc.extracted_data or {}).get("products") or []
    if isinstance(products, list) and products:
        tags.append("has_products:true")
    return tags


def _process_one(doc: Document) -> tuple[str, list[str], str | None]:
    """Build tags + summary for a single doc. Returns (status, tags, summary)."""
    system_tags = _build_system_tags(doc)
    extracted = doc.extracted_data or {}
    markdown = (extracted.get("markdown") or doc.content_markdown or "").strip()

    if not markdown:
        return ("no_markdown", system_tags, None)

    try:
        result = summarize_document(
            markdown,
            filename=doc.filename,
            file_type=doc.file_type,
            doc_type=doc.doc_type,
        )
    except SummarizerError as exc:
        logger.warning("doc %d: summarizer raised %s", doc.id, exc)
        return ("summarizer_error", system_tags, None)

    if result is None:
        return ("skipped_no_api_key_or_empty", system_tags, None)

    merged: list[str] = list(system_tags)
    if result.get("language"):
        merged.append(f"lang:{result['language']}")
    for tag in result.get("tags", []):
        if tag and tag not in merged:
            merged.append(tag)
    return ("ok", merged, result.get("summary"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Show plan, don't write.")
    parser.add_argument("--only-id", type=int, help="Process a single document by id.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-summarize even if tags+summary already present.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        q = db.query(Document)
        if args.only_id:
            q = q.filter(Document.id == args.only_id)
        else:
            q = q.filter(Document.status == "extracted")

        docs = q.all()
        if not args.only_id and not args.force:
            docs = [d for d in docs if not (d.tags and d.summary)]

        logger.info("found %d document(s) to process", len(docs))

        outcome_counts: dict[str, int] = {}
        for doc in docs:
            status, tags, summary = _process_one(doc)
            outcome_counts[status] = outcome_counts.get(status, 0) + 1
            logger.info(
                "doc %d (%s) → %s | %d tag(s) | summary=%s",
                doc.id,
                (doc.filename or "")[:50],
                status,
                len(tags),
                "yes" if summary else "no",
            )
            if not args.dry_run:
                doc.tags = tags or None
                doc.summary = summary
                db.add(doc)
                db.commit()

        logger.info("---")
        logger.info("done. outcome counts: %s", outcome_counts)
    finally:
        db.close()


if __name__ == "__main__":
    main()
