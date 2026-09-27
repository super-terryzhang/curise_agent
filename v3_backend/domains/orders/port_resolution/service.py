"""Persist and apply constrained port-resolution decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy.orm import Session

from domains.masterdata import repository as masterdata_repository
from domains.orders.models import Order
from domains.orders.port_resolution.resolver import PROMPT_VERSION, resolve_destination
from domains.orders.port_resolution.types import (
    PortCandidate,
    PortResolutionDecision,
    PortResolutionError,
)
from infrastructure.config import settings


@dataclass(frozen=True, slots=True)
class PortResolutionOutcome:
    status: Literal["matched", "unresolved", "failed"]
    port_id: int | None
    issue_code: str | None
    decision_id: str
    reason: str


def resolve_order_port(
    db: Session,
    order: Order,
    *,
    destination: str,
    source_code: str | None,
    api_key: str,
    model: str,
) -> PortResolutionOutcome:
    """Resolve one order without allowing provider failures to escape the PO."""
    ports = [
        port
        for port in masterdata_repository.list_ports(db)
        if port.status is True and port.country_id is not None
    ]
    candidates = [
        PortCandidate(id=port.id, name=port.name, country_id=port.country_id)
        for port in ports
        if port.country_id is not None
    ]
    decision_id = str(uuid4())
    decided_at = _utc_now_text()

    try:
        decision = resolve_destination(
            destination,
            candidates,
            api_key=api_key,
            model=model,
            timeout_ms=settings.LLM_PORT_RESOLUTION_TIMEOUT_MS,
            attempts=settings.LLM_PORT_RESOLUTION_ATTEMPTS,
        )
    except PortResolutionError as exc:
        _clear_unconfirmed_automatic_port(order)
        _store_state(
            order,
            destination=destination,
            source_code=source_code,
            decision_id=decision_id,
            decided_at=decided_at,
            status="unresolved",
            suggested_port_id=None,
            final_port_id=order.port_id,
            model=model,
            prompt_version=PROMPT_VERSION,
            reason=exc.detail,
            candidate_snapshot_hash=None,
            failure_code=exc.code,
        )
        db.flush()
        return PortResolutionOutcome(
            status="failed",
            port_id=order.port_id,
            issue_code="LLM_PORT_RESOLUTION_FAILED",
            decision_id=decision_id,
            reason=exc.detail,
        )

    if decision.status == "unmatched":
        _clear_unconfirmed_automatic_port(order)
        _store_decision_state(
            order,
            decision,
            destination=destination,
            source_code=source_code,
            decision_id=decision_id,
            decided_at=decided_at,
            status="unresolved",
            final_port_id=order.port_id,
            failure_code=None,
        )
        db.flush()
        return PortResolutionOutcome(
            status="unresolved",
            port_id=order.port_id,
            issue_code="LLM_PORT_UNRESOLVED",
            decision_id=decision_id,
            reason=decision.reason,
        )

    selected_port = masterdata_repository.get_port(db, decision.port_id or 0)
    if selected_port is not None:
        db.refresh(selected_port)
    if (
        selected_port is None
        or selected_port.status is not True
        or selected_port.country_id is None
    ):
        _clear_unconfirmed_automatic_port(order)
        _store_decision_state(
            order,
            decision,
            destination=destination,
            source_code=source_code,
            decision_id=decision_id,
            decided_at=decided_at,
            status="unresolved",
            final_port_id=order.port_id,
            failure_code="selected_port_invalid",
        )
        db.flush()
        return PortResolutionOutcome(
            status="failed",
            port_id=order.port_id,
            issue_code="LLM_PORT_RESOLUTION_FAILED",
            decision_id=decision_id,
            reason="selected_port_invalid",
        )

    order.port_id = selected_port.id
    order.country_id = selected_port.country_id
    order.port_resolution_reviewed_by = None
    order.port_resolution_reviewed_at = None
    _store_decision_state(
        order,
        decision,
        destination=destination,
        source_code=source_code,
        decision_id=decision_id,
        decided_at=decided_at,
        status="pending_review",
        final_port_id=selected_port.id,
        failure_code=None,
    )
    db.flush()
    return PortResolutionOutcome(
        status="matched",
        port_id=selected_port.id,
        issue_code=None,
        decision_id=decision_id,
        reason=decision.reason,
    )


def _store_decision_state(
    order: Order,
    decision: PortResolutionDecision,
    *,
    destination: str,
    source_code: str | None,
    decision_id: str,
    decided_at: str,
    status: Literal["pending_review", "unresolved"],
    final_port_id: int | None,
    failure_code: str | None,
) -> None:
    _store_state(
        order,
        destination=destination,
        source_code=source_code,
        decision_id=decision_id,
        decided_at=decided_at,
        status=status,
        suggested_port_id=decision.port_id,
        final_port_id=final_port_id,
        model=decision.model,
        prompt_version=decision.prompt_version,
        reason=decision.reason,
        candidate_snapshot_hash=decision.candidate_snapshot_hash,
        failure_code=failure_code,
    )


def _store_state(
    order: Order,
    *,
    destination: str,
    source_code: str | None,
    decision_id: str,
    decided_at: str,
    status: Literal["pending_review", "unresolved"],
    suggested_port_id: int | None,
    final_port_id: int | None,
    model: str,
    prompt_version: str,
    reason: str,
    candidate_snapshot_hash: str | None,
    failure_code: str | None,
) -> None:
    order.port_resolution_method = "llm"
    order.port_resolution_status = status
    order.port_resolution_data = {
        "source_destination": (destination or "").strip(),
        "source_port_code": (source_code or "").strip() or None,
        "suggested_port_id": suggested_port_id,
        "final_port_id": final_port_id,
        "model": model,
        "prompt_version": prompt_version,
        "reason": reason,
        "decision_id": decision_id,
        "decided_at": decided_at,
        "candidate_snapshot_hash": candidate_snapshot_hash,
        "failure_code": failure_code,
    }
    order.port_resolution_reviewed_by = None
    order.port_resolution_reviewed_at = None


def _clear_unconfirmed_automatic_port(order: Order) -> None:
    if (
        order.port_resolution_method == "llm"
        and order.port_resolution_status == "pending_review"
    ):
        order.port_id = None
        order.country_id = None


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
