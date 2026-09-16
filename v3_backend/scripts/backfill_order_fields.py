#!/usr/bin/env python3
"""Backfill the Order PO columns from legacy `extracted_data.metadata` JSON.

After Alembic migration `0002_orders_expand` runs, the new columns
(`po_number`, `ship_name`, `vendor_name`, `order_date`, `currency`,
`destination_port`, `field_evidence`) are empty for every pre-existing row.

This script copies values out of the JSON blob into the new columns.

Usage:

    # Safety first: dry run + sample diff
    python scripts/backfill_order_fields.py --dry-run --limit 100

    # Real run, resumable (writes progress to `.backfill_checkpoint`)
    python scripts/backfill_order_fields.py --batch-size 500

    # Resume from the last checkpoint
    python scripts/backfill_order_fields.py --resume

Design:
- IDEMPOTENT — running twice is a no-op (values already written stay put)
- DOES NOT TOUCH the JSON fields — pure write-forward to columns
- Reports a per-field discrepancy summary when `--dry-run`
- Commits every `--batch-size` rows so crashes resume cleanly
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Make domain imports work from CLI context
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from domains.orders.models import Order  # noqa: E402
from infrastructure.db.session import SessionLocal  # noqa: E402

logger = logging.getLogger("backfill")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

_COLUMN_TO_JSON_KEY: dict[str, str] = {
    "po_number": "po_number",
    "ship_name": "ship_name",
    "vendor_name": "vendor_name",
    "order_date": "order_date",
    "currency": "currency",
    "destination_port": "destination_port",
}


_CHECKPOINT_FILE = Path(".backfill_checkpoint")


def _read_checkpoint() -> int:
    if not _CHECKPOINT_FILE.exists():
        return 0
    try:
        return int(_CHECKPOINT_FILE.read_text().strip())
    except ValueError:
        return 0


def _write_checkpoint(last_id: int) -> None:
    _CHECKPOINT_FILE.write_text(str(last_id))


def _fields_from_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    out: dict[str, Any] = {}
    for column, json_key in _COLUMN_TO_JSON_KEY.items():
        value = metadata.get(json_key)
        if value not in (None, "", []):
            out[column] = value
    total = metadata.get("total_amount")
    if total not in (None, "", []):
        with contextlib.suppress(TypeError, ValueError):
            out["total_amount"] = float(total)
    return out


def _apply_to_order(order: Order, fields: dict[str, Any]) -> dict[str, Any]:
    """Write missing fields onto the Order; return dict of changes made."""
    changed: dict[str, Any] = {}
    for col, value in fields.items():
        if col == "total_amount":
            if order.total_amount is None:
                order.total_amount = value
                changed[col] = value
        else:
            if getattr(order, col, None) in (None, ""):
                setattr(order, col, value)
                changed[col] = value
    return changed


def run(
    *,
    dry_run: bool,
    batch_size: int,
    limit: int | None,
    resume: bool,
) -> None:
    start_id = _read_checkpoint() if resume else 0
    session: Session = SessionLocal()
    logger.info(
        "backfill start dry_run=%s start_id=%s batch_size=%s limit=%s",
        dry_run,
        start_id,
        batch_size,
        limit,
    )

    inspected = 0
    updated = 0
    skipped_no_metadata = 0
    change_totals: dict[str, int] = {}
    sample_diffs: list[dict[str, Any]] = []

    try:
        stmt = select(Order).where(Order.id > start_id).order_by(Order.id)
        if limit is not None:
            stmt = stmt.limit(limit)

        for order in session.execute(stmt).scalars():
            inspected += 1
            metadata = (
                (order.extraction_data or {}).get("metadata") if order.extraction_data else None
            )
            if not metadata:
                metadata = order.order_metadata
            candidate = _fields_from_metadata(metadata)
            if not candidate:
                skipped_no_metadata += 1
                continue

            changed = _apply_to_order(order, candidate)
            if changed:
                updated += 1
                for key in changed:
                    change_totals[key] = change_totals.get(key, 0) + 1
                if len(sample_diffs) < 5:
                    sample_diffs.append(
                        {
                            "order_id": order.id,
                            "changes": changed,
                        }
                    )

            if not dry_run and inspected % batch_size == 0:
                session.commit()
                _write_checkpoint(order.id)
                logger.info("committed batch ending at id=%s", order.id)

        if not dry_run:
            session.commit()
            if inspected:
                _write_checkpoint(order.id)
    finally:
        session.close()

    logger.info(
        "backfill done — inspected=%s updated=%s skipped_no_metadata=%s",
        inspected,
        updated,
        skipped_no_metadata,
    )
    logger.info("per-column updates: %s", json.dumps(change_totals, ensure_ascii=False))
    if sample_diffs:
        logger.info("sample diffs: %s", json.dumps(sample_diffs, ensure_ascii=False, indent=2))
    if dry_run:
        logger.info("DRY RUN — no rows persisted")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Do not commit changes")
    ap.add_argument("--batch-size", type=int, default=500)
    ap.add_argument("--limit", type=int, default=None, help="Inspect at most N rows")
    ap.add_argument("--resume", action="store_true", help="Resume from last checkpoint")
    args = ap.parse_args()

    try:
        run(
            dry_run=args.dry_run,
            batch_size=args.batch_size,
            limit=args.limit,
            resume=args.resume,
        )
    except Exception:
        logger.exception("backfill failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
