"""Cleanup duplicate Orders per Document (HF1 follow-up, 2026-06-22).

Prod incident 2026-06-22 (see docs/current_progress/2026-06-22): a
double-click on "确认建单" before the idempotency guard landed created
multiple Order rows per Document. `find_order_id_for_document` then
crashed with `MultipleResultsFound` and the document list endpoint
returned 500 (browser misreported as CORS).

The hotfix:
    1. `get_by_document_id` now tolerates multi-row state by returning
       the most-recent row → list endpoint stops crashing.
    2. `create_from_document` is now idempotent → no NEW duplicates can
       form.

This script cleans up the EXISTING duplicates. Strategy:
    - For each document_id with > 1 linked Order, keep the most recently
      created one; the older ones are deleted along with their cascaded
      children (Inquiry → InquirySupplier).
    - Default mode is `--dry-run`: prints what WOULD be deleted, makes
      no changes.
    - `--apply` actually deletes after printing the plan.

Usage:
    # Dry run (safe, just shows what would happen):
    python scripts/cleanup_duplicate_orders.py

    # Actually delete after reviewing dry-run output:
    python scripts/cleanup_duplicate_orders.py --apply

    # In prod against Supabase: cd v3_backend && DATABASE_URL=postgresql://...
    # python scripts/cleanup_duplicate_orders.py --apply
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.orm import Session

# Make the script runnable from the repo root: `python v3_backend/scripts/...`
# OR from inside v3_backend/: `python scripts/...`.
import os
_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_DIR)

from domains.orders.models import Order  # noqa: E402
from infrastructure.db.session import SessionLocal  # noqa: E402


def find_duplicates(db: Session) -> dict[int, list[Order]]:
    """Return {document_id: [Order, Order, ...]} for documents with > 1
    Order. List is ordered newest-first (so [0] is the keeper)."""
    # First find document_ids with > 1 order — cheap aggregation.
    rows = (
        db.execute(
            select(Order.document_id, func.count(Order.id))
            .where(Order.document_id.is_not(None))
            .group_by(Order.document_id)
            .having(func.count(Order.id) > 1)
        )
        .all()
    )
    dup_doc_ids = [r[0] for r in rows]
    if not dup_doc_ids:
        return {}

    # Load full Order rows for those documents, sorted within each.
    orders = (
        db.execute(
            select(Order)
            .where(Order.document_id.in_(dup_doc_ids))
            .order_by(Order.document_id, Order.created_at.desc())
        )
        .scalars()
        .all()
    )

    out: dict[int, list[Order]] = defaultdict(list)
    for o in orders:
        out[o.document_id].append(o)  # type: ignore[index]
    return dict(out)


def plan(duplicates: dict[int, list[Order]]) -> list[dict[str, Any]]:
    """Return the deletion plan: per duplicate set, keeper + losers."""
    out = []
    for doc_id, orders in duplicates.items():
        keeper, *losers = orders
        out.append(
            {
                "document_id": doc_id,
                "keep_order_id": keeper.id,
                "keep_created_at": keeper.created_at,
                "delete_order_ids": [o.id for o in losers],
                "delete_created_at": [o.created_at for o in losers],
            }
        )
    return out


def apply(db: Session, deletion_plan: list[dict[str, Any]]) -> dict[str, int]:
    """Execute the plan. Returns counts of what was deleted.

    Relies on FK ON DELETE CASCADE (configured in the inquiry tables) to
    drop dependent Inquiry / InquirySupplier rows automatically. If the
    cascades aren't set up that way, this raises and you get to fix the
    cascade configuration before re-running — much safer than silently
    leaving orphaned dependent rows.
    """
    counts = {"orders": 0, "groups_with_duplicates": len(deletion_plan)}
    for entry in deletion_plan:
        for oid in entry["delete_order_ids"]:
            order = db.get(Order, oid)
            if order is None:
                continue
            db.delete(order)
            counts["orders"] += 1
    db.commit()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clean up duplicate Order rows per Document.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete. Without this, runs as dry-run.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        duplicates = find_duplicates(db)
        if not duplicates:
            print("✅ No duplicate Orders found. Database is clean.")
            return 0

        print(
            f"⚠️  Found duplicates in {len(duplicates)} documents:\n"
        )
        deletion_plan = plan(duplicates)
        total_deletions = 0
        for entry in deletion_plan:
            print(
                f"  document_id={entry['document_id']}  "
                f"KEEP order #{entry['keep_order_id']} ({entry['keep_created_at']})"
            )
            for oid, ts in zip(
                entry["delete_order_ids"], entry["delete_created_at"]
            ):
                print(f"    DELETE order #{oid} ({ts})")
                total_deletions += 1

        print(f"\nTotal Orders to delete: {total_deletions}")

        if not args.apply:
            print(
                "\nDRY RUN — nothing changed. Re-run with --apply to "
                "actually delete."
            )
            return 0

        print("\nApplying deletes…")
        counts = apply(db, deletion_plan)
        print(
            f"✅ Deleted {counts['orders']} Order rows across "
            f"{counts['groups_with_duplicates']} document groups."
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
