"""Read-only shadow audit for unit conversion coverage on one PO."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from domains.masterdata import service as masterdata_service
from domains.orders.models import Order
from infrastructure.db.session import SessionLocal


def _set_transaction_read_only(db: Session) -> None:
    """Protect production audits at the database transaction boundary."""

    bind = db.get_bind() if hasattr(db, "get_bind") else db.bind
    if bind.dialect.name == "postgresql":
        db.execute(text("SET TRANSACTION READ ONLY"))


def _business_date(order: Order) -> date | None:
    value = str(order.delivery_date or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _quantity_text(value: Decimal) -> str:
    return format(value, ".10f")


def audit_order(
    db: Session,
    order: Order,
    *,
    include_drafts: bool = False,
) -> dict[str, Any]:
    """Evaluate stored match snapshots without mutating the order or rules."""

    products = list(order.products or [])
    results = list(order.match_results or [])
    total = max(len(products), len(results))
    counts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    matched_rows = 0
    converted_rows = 0
    same_rows = 0
    review_rows = 0
    business_date = _business_date(order)

    for index in range(total):
        source = products[index] if index < len(products) else {}
        result = results[index] if index < len(results) else {}
        product_code = source.get("product_code") or result.get("product_code")
        if result.get("match_status") != "matched":
            counts["PRODUCT_NOT_MATCHED"] += 1
            rows.append(
                {
                    "row_index": index + 1,
                    "product_code": product_code,
                    "status": "not_matched",
                    "issue_code": "PRODUCT_NOT_MATCHED",
                }
            )
            continue

        matched_rows += 1
        matched = result.get("matched_product") or {}
        evaluated = masterdata_service.evaluate_unit_conversion(
            db,
            product_id=matched.get("id"),
            source_system="oracle",
            source_quantity=source.get("quantity", result.get("quantity")),
            source_unit=str(source.get("unit") or result.get("unit") or ""),
            target_unit=str(matched.get("unit") or ""),
            business_date=business_date,
            product_unit=matched.get("unit"),
            product_unit_size=matched.get("unit_size"),
            product_pack_size=matched.get("pack_size"),
            include_drafts=include_drafts,
        )
        status = evaluated.get("status")
        row_output: dict[str, Any] = {
            "row_index": index + 1,
            "product_code": product_code,
            "status": status,
        }
        if status in {"converted", "same"}:
            if status == "converted":
                converted_rows += 1
            else:
                same_rows += 1
            evidence = evaluated.get("conversion_evidence") or {}
            row_output.update(
                target_quantity=_quantity_text(evaluated["target_quantity"]),
                target_unit=evaluated["target_unit"],
                rule_id=evidence.get("rule_id"),
                rule_status="verified" if evidence.get("verified") else "draft",
            )
        else:
            review_rows += 1
            issue_code = evaluated.get("issue_code") or "UNIT_CONVERSION_REQUIRED"
            counts[issue_code] += 1
            row_output.update(
                issue_code=issue_code,
                message=evaluated.get("message"),
            )
        rows.append(row_output)

    return {
        "po_number": order.po_number,
        "order_id": order.id,
        "business_date": business_date.isoformat() if business_date else None,
        "include_drafts": include_drafts,
        "total_rows": total,
        "matched_rows": matched_rows,
        "unmatched_rows": total - matched_rows,
        "converted_rows": converted_rows,
        "same_rows": same_rows,
        "review_rows": review_rows,
        "issue_counts": dict(sorted(counts.items())),
        "rows": rows,
        "audited_at": datetime.now().isoformat(timespec="seconds"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--po-number", required=True)
    parser.add_argument("--include-drafts", action="store_true")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        _set_transaction_read_only(db)
        order = db.execute(
            select(Order)
            .where(Order.po_number == args.po_number)
            .order_by(Order.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if order is None:
            raise LookupError(f"PO {args.po_number} 不存在")
        output = audit_order(db, order, include_drafts=args.include_drafts)
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        db.rollback()
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI must emit machine-readable failure
        db.rollback()
        print(
            json.dumps(
                {"status": "error", "error": type(exc).__name__, "message": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
