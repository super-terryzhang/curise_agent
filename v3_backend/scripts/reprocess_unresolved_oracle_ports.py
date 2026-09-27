"""Resolve one historical Oracle PO port with dry-run-first safety guards.

Examples:
    python -m scripts.reprocess_unresolved_oracle_ports --po-number PO168798CCI
    python -m scripts.reprocess_unresolved_oracle_ports \
        --po-number PO168798CCI --apply --expected-order-id 73
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from domains.orders.models import Order
from domains.orders.oracle_models import OraclePOImport
from domains.orders.port_resolution.service import resolve_order_port
from infrastructure.config import settings


class RepairGuardError(ValueError):
    """Raised before any model or database mutation when a guard fails."""


_EXACT_PO_NUMBER = re.compile(r"^[A-Za-z0-9_-]+$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--po-number", required=True, help="One exact Oracle PO number")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the proposal and run stages 5-8; omitted means dry-run",
    )
    parser.add_argument(
        "--expected-order-id",
        type=int,
        help="Required with --apply; prevents writing a newly/relinked order",
    )
    return parser


def _snapshot(order: Order) -> dict[str, Any]:
    resolution = dict(order.port_resolution_data or {})
    return {
        "order_id": order.id,
        "destination": order.destination_port,
        "port_id": order.port_id,
        "country_id": order.country_id,
        "group_id": order.group_id,
        "order_status": order.status,
        "processing_error": order.processing_error,
        "resolution_method": order.port_resolution_method,
        "resolution_status": order.port_resolution_status,
        "decision_id": resolution.get("decision_id"),
        "suggested_port_id": resolution.get("suggested_port_id"),
        "failure_code": resolution.get("failure_code"),
    }


def _source_snapshot(source: OraclePOImport) -> dict[str, Any]:
    return {
        "source_key": source.source_key,
        "po_number": source.po_number,
        "order_id": source.order_id,
        "status": source.status,
        "stage": source.stage,
    }


def _find_source(db: Session, po_number: str) -> OraclePOImport:
    rows = (
        db.query(OraclePOImport)
        .filter(OraclePOImport.po_number == po_number)
        .order_by(OraclePOImport.created_at.desc())
        .all()
    )
    if len(rows) != 1:
        raise RepairGuardError(
            f"exactly one Oracle import is required; found {len(rows)}"
        )
    if rows[0].order_id is None:
        raise RepairGuardError("Oracle import has no linked order")
    return rows[0]


def _validate_target(po_number: str, *, apply: bool, expected_order_id: int | None) -> str:
    normalized = po_number.strip()
    if not normalized or not _EXACT_PO_NUMBER.fullmatch(normalized):
        raise RepairGuardError("one single exact PO number is required")
    if apply and expected_order_id is None:
        raise RepairGuardError("--apply requires --expected-order-id")
    if expected_order_id is not None and expected_order_id <= 0:
        raise RepairGuardError("--expected-order-id must be positive")
    return normalized


def _default_pipeline_runner(order_id: int) -> dict[str, Any]:
    from domains.orders.automation import automatic_order_pipeline

    return automatic_order_pipeline(order_id)


def _sync_oracle_source_after_apply(
    session_factory: sessionmaker[Session],
    *,
    source_key: str,
    order_id: int,
    resolution_status: str,
    issue_code: str | None,
) -> dict[str, Any]:
    with session_factory() as db:
        source = db.get(OraclePOImport, source_key)
        order = db.get(Order, order_id)
        if source is None or order is None:
            raise RepairGuardError("linked Oracle source or order disappeared")
        findings = list((order.anomaly_data or {}).get("findings") or [])
        if resolution_status == "matched":
            requires_review = bool(
                (order.anomaly_data or {}).get("requires_human_review")
            )
            source.status = "needs_review" if requires_review else "completed"
            source.stage = "anomaly" if requires_review else "completed"
            source.issues = findings
            source.error_code = order.processing_error if order.status == "error" else None
        else:
            source.status = "needs_review"
            source.stage = "matching"
            source.issues = [{"code": issue_code or "LLM_PORT_UNRESOLVED"}]
            source.error_code = None
        db.commit()
        db.refresh(order)
        return _snapshot(order)


def run_repair(
    *,
    po_number: str,
    apply: bool,
    expected_order_id: int | None,
    session_factory: sessionmaker[Session] | None = None,
    pipeline_runner: Callable[[int], dict[str, Any]] | None = None,
    emit: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Evaluate or apply one exact Oracle PO repair using the shared service."""
    normalized = _validate_target(
        po_number,
        apply=apply,
        expected_order_id=expected_order_id,
    )
    if session_factory is None:
        from infrastructure.db.session import SessionLocal

        session_factory = SessionLocal
    pipeline_runner = pipeline_runner or _default_pipeline_runner
    emit = emit or (lambda payload: print(json.dumps(payload, ensure_ascii=False)))

    with session_factory() as db:
        source = _find_source(db, normalized)
        order = db.get(Order, source.order_id)
        if order is None:
            raise RepairGuardError("linked order does not exist")
        if apply and order.id != expected_order_id:
            raise RepairGuardError(
                f"expected-order-id mismatch: expected {expected_order_id}, found {order.id}"
            )
        if order.port_resolution_status not in {None, "unresolved"}:
            raise RepairGuardError(
                f"order port state is {order.port_resolution_status}; only unresolved orders can be repaired"
            )
        if order.port_resolution_status is None and order.port_id is not None:
            raise RepairGuardError(
                "legacy order already has a selected port; use the normal review UI"
            )
        destination = (order.destination_port or "").strip()
        if not destination:
            raise RepairGuardError("order has no destination text")

        before = _snapshot(order)
        source_before = _source_snapshot(source)
        outcome = resolve_order_port(
            db,
            order,
            destination=destination,
            source_code=(order.order_metadata or {}).get("location_code"),
            api_key=settings.GOOGLE_API_KEY,
            model=settings.LLM_PORT_RESOLUTION_MODEL,
        )
        proposed = _snapshot(order)

        if not apply:
            db.rollback()
            after_order = db.get(Order, order.id)
            if after_order is None:
                raise RepairGuardError("linked order disappeared during dry-run")
            after = _snapshot(after_order)
            result = {
                "mode": "dry_run",
                "source": source_before,
                "before": before,
                "proposed": proposed,
                "after": after,
                "outcome": {
                    "status": outcome.status,
                    "port_id": outcome.port_id,
                    "issue_code": outcome.issue_code,
                },
                "assertions": {
                    "database_unchanged": after == before,
                    "expected_order_id_matches": True,
                },
            }
            emit(result)
            return result

        db.commit()
        order_id = order.id
        source_key = source.source_key

    if outcome.status == "matched":
        pipeline_runner(order_id)
    after = _sync_oracle_source_after_apply(
        session_factory,
        source_key=source_key,
        order_id=order_id,
        resolution_status=outcome.status,
        issue_code=outcome.issue_code,
    )
    result = {
        "mode": "apply",
        "source": source_before,
        "before": before,
        "proposed": proposed,
        "after": after,
        "outcome": {
            "status": outcome.status,
            "port_id": outcome.port_id,
            "issue_code": outcome.issue_code,
        },
        "assertions": {
            "database_unchanged": False,
            "expected_order_id_matches": order_id == expected_order_id,
            "applied_port_matches_proposal": after["port_id"] == proposed["port_id"],
        },
    }
    emit(result)
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_repair(
            po_number=args.po_number,
            apply=args.apply,
            expected_order_id=args.expected_order_id,
        )
    except RepairGuardError as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False))
        return 2
    return 0 if result["outcome"]["status"] == "matched" else 3


if __name__ == "__main__":
    raise SystemExit(main())
