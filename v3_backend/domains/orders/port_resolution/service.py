"""Persist and apply constrained port-resolution decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy.orm import Session

from domains.masterdata import repository as masterdata_repository
from domains.orders.errors import BadRequest, NotFound, StatusConflict
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


@dataclass(frozen=True, slots=True)
class PortResolutionReviewTransition:
    order: Order
    changed: bool
    continuation_key: str | None = None


def confirm_order_port(
    db: Session,
    *,
    order_id: int,
    user_id: int,
    is_admin: bool,
    decision_id: str,
    reviewer_id: int,
) -> PortResolutionReviewTransition:
    """Confirm the current AI decision under a row lock."""
    order = _lock_order_for_user(db, order_id, user_id, is_admin)
    data = _validated_current_decision(order, decision_id)
    final_port_id = data.get("final_port_id")
    if final_port_id is None or order.port_id != final_port_id:
        raise StatusConflict("目标港口已变更，请刷新后重新审核")
    if order.port_resolution_status == "confirmed":
        return PortResolutionReviewTransition(order=order, changed=False)
    if order.port_resolution_status != "pending_review":
        raise StatusConflict("当前港口判定状态不能确认")

    order.port_resolution_status = "confirmed"
    order.port_resolution_reviewed_by = reviewer_id
    order.port_resolution_reviewed_at = datetime.now(UTC)
    db.flush()
    return PortResolutionReviewTransition(order=order, changed=True)


def override_order_port(
    db: Session,
    *,
    order_id: int,
    user_id: int,
    is_admin: bool,
    decision_id: str,
    port_id: int,
    reviewer_id: int,
) -> PortResolutionReviewTransition:
    """Apply one audited manual port override under a row lock."""
    order = _lock_order_for_user(db, order_id, user_id, is_admin)
    data = _validated_current_decision(order, decision_id)
    continuation_key = f"{decision_id}:{port_id}"
    if (
        order.port_resolution_status == "overridden"
        and data.get("final_port_id") == port_id
        and data.get("continuation_key") == continuation_key
        and order.port_id == port_id
    ):
        return PortResolutionReviewTransition(
            order=order,
            changed=False,
            continuation_key=continuation_key,
        )
    if order.port_resolution_status not in {"pending_review", "unresolved"}:
        raise StatusConflict("当前港口判定状态不能改选")

    port = masterdata_repository.get_port(db, port_id)
    if port is None or port.status is not True or port.country_id is None:
        raise BadRequest("请选择启用且已配置国家的港口")

    order.port_id = port.id
    order.country_id = port.country_id
    order.port_resolution_status = "overridden"
    order.port_resolution_reviewed_by = reviewer_id
    order.port_resolution_reviewed_at = datetime.now(UTC)
    order.port_resolution_data = {
        **data,
        "final_port_id": port.id,
        "continuation_key": continuation_key,
        "continuation_status": "pending",
    }
    db.flush()
    return PortResolutionReviewTransition(
        order=order,
        changed=True,
        continuation_key=continuation_key,
    )


def mark_override_continuation_complete(
    db: Session,
    *,
    order_id: int,
    continuation_key: str,
) -> Order:
    """Record completion only when the same override is still current."""
    order = db.query(Order).filter(Order.id == order_id).with_for_update().one()
    data = dict(order.port_resolution_data or {})
    if data.get("continuation_key") == continuation_key:
        order.port_resolution_data = {**data, "continuation_status": "completed"}
        db.commit()
        db.refresh(order)
    return order


def apply_manual_port_override(
    order: Order,
    *,
    port_id: int,
    country_id: int,
    reviewer_id: int,
    source: str,
) -> bool:
    """Apply shared manual-review metadata without running downstream stages."""
    if not (
        order.port_resolution_method == "llm"
        and order.port_resolution_status == "pending_review"
    ):
        return False
    data = dict(order.port_resolution_data or {})
    order.port_id = port_id
    order.country_id = country_id
    order.port_resolution_status = "overridden"
    order.port_resolution_reviewed_by = reviewer_id
    order.port_resolution_reviewed_at = datetime.now(UTC)
    order.port_resolution_data = {
        **data,
        "final_port_id": port_id,
        "manual_override_source": source,
    }
    return True


def _lock_order_for_user(
    db: Session, order_id: int, user_id: int, is_admin: bool
) -> Order:
    query = db.query(Order).filter(Order.id == order_id)
    if not is_admin:
        query = query.filter(Order.user_id == user_id)
    order = query.with_for_update().one_or_none()
    if order is None:
        raise NotFound("订单不存在")
    return order


def _validated_current_decision(order: Order, decision_id: str) -> dict:
    data = dict(order.port_resolution_data or {})
    if order.port_resolution_method != "llm" or not data.get("decision_id"):
        raise BadRequest("该订单没有可审核的 AI 港口判定")
    if data["decision_id"] != decision_id:
        raise StatusConflict("港口判定已更新，请刷新后重试")
    return data


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
