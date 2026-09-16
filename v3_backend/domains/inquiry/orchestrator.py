"""Inquiry orchestration — runs an inquiry across all suppliers for one Order.

The orchestrator is sync (designed to be submitted to a background runner)
and uses a thread pool to fan out per-supplier work. It pushes progress
through `InquiryProgressSink` (no direct dependency on SSE / Agent runtime
— see ADR-0002).

Public surface:
- `run_inquiry(order_id, sink, ...)` — full multi-supplier run.
- `run_inquiry_for_group(group_id, sink, ...)` — rebuilds the arrangement
  match and creates a new immutable inquiry version with source snapshots.
- `run_inquiry_for_supplier(order_id, supplier_id, sink, ...)` — re-do one.
- `pre_analyze(order_id)` — read-only readiness summary (returns InquiryState).
- `request_cancel(order_id)` — mark the inquiry's `cancel_requested_at`.

Per-supplier work lives in `_supplier_worker.py` to keep this file focused
on coordination + run-level state transitions.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from domains.inquiry import _state, _supplier_worker
from domains.inquiry import repository as repo
from domains.inquiry import service as inquiry_service
from domains.inquiry.errors import BadRequest, NotFound
from domains.inquiry.models import Inquiry, InquirySupplier
from domains.inquiry.schemas import InquiryState
from domains.inquiry.sinks import InquiryProgressSink, NullSink
from domains.inquiry.template_selector import select_template
from domains.orders import Order
from infrastructure.db import session as session_module

logger = logging.getLogger(__name__)


# ─── Public surface ───────────────────────────────────────────


def pre_analyze(db: Session, order_id: int) -> InquiryState:
    """Group products by supplier + resolve templates without running the engine.

    Creates the Inquiry row + supplier rows on first call. If the inquiry has
    already run (in_progress / completed / cancelled / error), returns the
    current state untouched — pre-analysis is a kick-off helper, not a reset.
    """
    from domains.masterdata import service as masterdata_service

    inquiry = repo.get_inquiry_by_order(db, order_id)
    if inquiry is not None and inquiry.status != "pending":
        return _state.to_state(db, inquiry)

    order = _load_order(db, order_id)
    groups = _group_products_by_supplier(order)
    unassigned = sum(1 for r in (order.match_results or []) if not _supplier_id_of(r))

    inquiry = _state.ensure_inquiry(db, order_id=order_id)
    inquiry.status = "pending"
    inquiry.started_at = None
    inquiry.completed_at = None
    inquiry.total_elapsed_seconds = None
    inquiry.supplier_count = len(groups)
    inquiry.unassigned_count = unassigned

    all_templates = inquiry_service.list_supplier_templates(db)
    metadata = _order_metadata(order)
    currency = metadata.get("currency", "")
    for supplier_id, products in groups.items():
        template, method, _candidates = select_template(supplier_id, all_templates)
        supplier_dict = masterdata_service.get_supplier(db, supplier_id) or {}
        _state.upsert_supplier(
            db,
            inquiry_id=inquiry.id,
            supplier_id=supplier_id,
            fields={
                "supplier_name": supplier_dict.get("name") or f"供应商 #{supplier_id}",
                "supplier_info": _supplier_worker.supplier_info(supplier_dict),
                "missing_fields": _supplier_worker.missing_supplier_fields(supplier_dict),
                "product_count": len(products),
                "subtotal": _supplier_worker.compute_subtotal(products),
                "currency": currency,
                "template_id": template.id if template else None,
                "template_name": template.template_name if template else None,
                "template_selection_method": method,
                "status": "pending",
            },
        )
    db.commit()
    db.refresh(inquiry)
    return _state.to_state(db, inquiry)


def run_inquiry(
    order_id: int,
    *,
    sink: InquiryProgressSink | None = None,
    template_overrides: dict[int, int] | None = None,
    max_workers: int = 4,
) -> InquiryState:
    """Run the full inquiry pipeline for one order across all suppliers.

    Opens its own DB session (via `infrastructure.db.session.SessionLocal`)
    so it can be invoked off-thread. Returns the final state once all
    workers have finished. Raises if the order doesn't exist.
    """
    sink = sink or NullSink()
    overrides = template_overrides or {}
    overall_start = time.time()

    db = session_module.SessionLocal()
    try:
        order = _load_order(db, order_id)
        groups = _group_products_by_supplier(order)
        unassigned = sum(1 for r in (order.match_results or []) if not _supplier_id_of(r))

        inquiry = _state.ensure_inquiry(db, order_id=order_id)
        _state.reset_for_run(
            db,
            inquiry,
            started_at=datetime.utcnow(),
            supplier_count=len(groups),
            unassigned_count=unassigned,
        )
        inquiry.heartbeat_at = datetime.utcnow()
        db.commit()
        inquiry_id = inquiry.id

        sink.emit(
            {
                "type": "run_started",
                "order_id": order_id,
                "supplier_count": len(groups),
                "unassigned_count": unassigned,
            }
        )

        if not groups:
            inquiry.status = "completed"
            inquiry.completed_at = datetime.utcnow()
            inquiry.total_elapsed_seconds = round(time.time() - overall_start, 1)
            db.commit()
            sink.emit({"type": "run_completed", "supplier_count": 0})
            return _state.to_state(db, inquiry)

        # Build metadata per-supplier in the main thread (SQLAlchemy sessions
        # aren't safe to share across pool workers — the read-only master-data
        # lookups happen here, then the resulting dict is handed to the worker).
        per_supplier_metadata = {
            sid: _order_metadata(order, db=db, supplier_id=sid)
            for sid in groups
        }
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _supplier_worker.run_supplier_safe,
                    inquiry_id=inquiry_id,
                    supplier_id=sid,
                    products=prods,
                    metadata=per_supplier_metadata[sid],
                    sink=sink,
                    template_id_override=overrides.get(sid),
                ): sid
                for sid, prods in groups.items()
            }
            for fut in as_completed(futures):
                sid = futures[fut]
                try:
                    fut.result()
                except Exception as exc:
                    logger.error("inquiry: supplier %d crashed: %s", sid, exc, exc_info=True)

        db.refresh(inquiry)
        suppliers = repo.list_inquiry_suppliers(db, inquiry.id)
        statuses = {s.status for s in suppliers}
        if sink.should_cancel():
            inquiry.status = "cancelled"
        elif statuses & {"error"}:
            inquiry.status = "error"
        else:
            inquiry.status = "completed"
        inquiry.completed_at = datetime.utcnow()
        inquiry.total_elapsed_seconds = round(time.time() - overall_start, 1)
        db.commit()

        terminal_type = (
            "run_completed" if inquiry.status == "completed" else f"run_{inquiry.status}"
        )
        sink.emit(
            {
                "type": terminal_type,
                "supplier_count": len(suppliers),
                "elapsed_seconds": inquiry.total_elapsed_seconds,
            }
        )
        return _state.to_state(db, inquiry)
    finally:
        db.close()


def queue_inquiry_for_group(db: Session, group_id: int) -> InquiryState:
    """Persist one arrangement run before handing it to a background worker.

    Locking the oldest member serializes version allocation. Repeated clicks
    while that version is pending/running return the same durable work item.
    """
    orders = (
        db.query(Order)
        .filter(Order.group_id == group_id)
        .order_by(Order.created_at.asc())
        .with_for_update()
        .all()
    )
    if not orders:
        raise BadRequest(f"分组 #{group_id} 没有订单")
    latest = repo.get_latest_inquiry_by_group(db, group_id)
    if latest is not None and latest.status in {"pending", "in_progress"}:
        return _state.to_state(db, latest)
    inquiry = _state.create_group_inquiry(
        db,
        order_id=orders[0].id,
        group_id=group_id,
        member_snapshot=[_member_snapshot(order) for order in orders],
        match_snapshot=[],
        unmatched_items=[],
    )
    inquiry.next_retry_at = datetime.utcnow()
    db.commit()
    db.refresh(inquiry)
    return _state.to_state(db, inquiry)


def run_inquiry_for_group(
    group_id: int,
    *,
    inquiry_id: int | None = None,
    sink: InquiryProgressSink | None = None,
    template_overrides: dict[int, int] | None = None,
    max_workers: int = 4,
) -> InquiryState:
    """Re-match the full arrangement and create a new immutable inquiry version.

    Equal SKUs remain separate source lines. Only matched rows with a supplier
    enter supplier workbooks; every excluded row is persisted with its PO/line
    identity and reason in the same version.
    """
    sink = sink or NullSink()
    overrides = template_overrides or {}
    overall_start = time.time()

    db = session_module.SessionLocal()
    claimed_inquiry_id: int | None = None
    try:
        from domains.orders import service as orders_service

        group_meta = orders_service.find_order_group_meta(db, group_id)
        if group_meta is None:
            raise NotFound(f"分组 #{group_id} 不存在")

        orders = (
            db.query(Order)
            .filter(Order.group_id == group_id)
            .order_by(Order.created_at.asc())
            .all()
        )
        if not orders:
            raise BadRequest(f"分组 #{group_id} 没有订单")

        anchor = orders[0]
        member_snapshot = [_member_snapshot(order) for order in orders]

        if inquiry_id is None:
            inquiry = _state.create_group_inquiry(
                db,
                order_id=anchor.id,
                group_id=group_id,
                member_snapshot=member_snapshot,
                match_snapshot=[],
                unmatched_items=[],
            )
        else:
            inquiry = (
                db.query(Inquiry)
                .filter(Inquiry.id == inquiry_id, Inquiry.group_id == group_id)
                .with_for_update()
                .one_or_none()
            )
            if inquiry is None:
                raise NotFound(f"询价任务 #{inquiry_id} 不存在")
            if inquiry.status not in {"pending", "in_progress"}:
                return _state.to_state(db, inquiry)
            stale_before = datetime.utcnow() - timedelta(minutes=15)
            if (
                inquiry.status == "in_progress"
                and inquiry.heartbeat_at is not None
                and inquiry.heartbeat_at > stale_before
            ):
                return _state.to_state(db, inquiry)

        claimed_inquiry_id = inquiry.id
        inquiry.status = "in_progress"
        inquiry.run_attempts = (inquiry.run_attempts or 0) + 1
        inquiry.started_at = datetime.utcnow()
        inquiry.heartbeat_at = inquiry.started_at
        inquiry.next_retry_at = None
        inquiry.completed_at = None
        inquiry.error_message = None
        db.commit()

        try:
            arrangement_match = orders_service.match_order_group(db, group_id)
        except ValueError as exc:
            # A failed attempt is still a version: the user can see exactly
            # when it failed without damaging the previous successful version.
            inquiry.status = "error"
            inquiry.completed_at = datetime.utcnow()
            inquiry.heartbeat_at = inquiry.completed_at
            inquiry.error_message = str(exc)
            db.commit()
            raise BadRequest(str(exc)) from exc

        merged_groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
        unavailable: list[dict[str, Any]] = []
        for result in arrangement_match["items"]:
            sid = _supplier_id_of(result)
            matched_product = result.get("matched_product") or {}
            purchase_period = matched_product.get("purchase_price_period") or {}
            if result.get("match_status") != "matched":
                unavailable.append(result)
            elif row_issue := _inquiry_row_issue(result):
                result["match_reason"] = row_issue
                result["inquiry_eligibility"] = "excluded"
                unavailable.append(result)
            elif sid is None:
                result["match_reason"] = "匹配商品未配置供应商"
                result["inquiry_eligibility"] = "excluded"
                unavailable.append(result)
            elif (
                purchase_period.get("source") == "period"
                and purchase_period.get("amount") is None
            ):
                result["match_reason"] = purchase_period.get("warning") or "采购价期间无效"
                result["inquiry_eligibility"] = "excluded"
                unavailable.append(result)
            else:
                result["inquiry_eligibility"] = "included"
                merged_groups[sid].append(result)

        inquiry.member_snapshot = member_snapshot
        inquiry.match_snapshot = arrangement_match["items"]
        inquiry.unmatched_items = unavailable
        _state.reset_for_run(
            db,
            inquiry,
            started_at=datetime.utcnow(),
            supplier_count=len(merged_groups),
            unassigned_count=len(unavailable),
        )
        db.commit()
        inquiry_id = inquiry.id

        sink.emit(
            {
                "type": "run_started",
                "order_id": anchor.id,
                "group_id": group_id,
                "supplier_count": len(merged_groups),
                "unassigned_count": len(unavailable),
                "order_count": len(orders),
                "inquiry_id": inquiry_id,
                "version": inquiry.version,
            }
        )

        if not merged_groups:
            inquiry.status = "unmatched"
            inquiry.completed_at = datetime.utcnow()
            inquiry.heartbeat_at = inquiry.completed_at
            inquiry.total_elapsed_seconds = round(time.time() - overall_start, 1)
            db.commit()
            sink.emit(
                {
                    "type": "run_unmatched",
                    "supplier_count": 0,
                    "unassigned_count": len(unavailable),
                }
            )
            return _state.to_state(db, inquiry)

        # Per-supplier metadata: build from anchor, then overlay group
        # fields. `_order_metadata` does all the master-data lookups in
        # the main thread (SQLAlchemy session not thread-safe).
        per_supplier_metadata: dict[int, dict[str, Any]] = {}
        po_numbers = [o.po_number for o in orders if o.po_number]
        merged_po = " / ".join(po_numbers) if po_numbers else None
        for sid in merged_groups:
            md = _order_metadata(anchor, db=db, supplier_id=sid)
            if group_meta["ship_name"]:
                md["ship_name"] = group_meta["ship_name"]
            if group_meta["loading_date"]:
                md["loading_date"] = group_meta["loading_date"]
            if merged_po:
                md["po_number"] = merged_po
            per_supplier_metadata[sid] = md

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _supplier_worker.run_supplier_safe,
                    inquiry_id=inquiry_id,
                    supplier_id=sid,
                    products=prods,
                    metadata=per_supplier_metadata[sid],
                    sink=sink,
                    template_id_override=overrides.get(sid),
                ): sid
                for sid, prods in merged_groups.items()
            }
            for fut in as_completed(futures):
                sid = futures[fut]
                try:
                    fut.result()
                except Exception as exc:
                    logger.error(
                        "inquiry-group: supplier %d crashed: %s",
                        sid,
                        exc,
                        exc_info=True,
                    )
                inquiry.heartbeat_at = datetime.utcnow()
                db.commit()

        db.refresh(inquiry)
        suppliers = repo.list_inquiry_suppliers(db, inquiry.id)
        statuses = {s.status for s in suppliers}
        if sink.should_cancel():
            inquiry.status = "cancelled"
        elif statuses & {"error"} and statuses & {"completed"}:
            inquiry.status = "partial"
        elif statuses & {"error"}:
            inquiry.status = "error"
        elif unavailable:
            inquiry.status = "partial"
        else:
            inquiry.status = "completed"
        inquiry.completed_at = datetime.utcnow()
        inquiry.heartbeat_at = inquiry.completed_at
        inquiry.total_elapsed_seconds = round(time.time() - overall_start, 1)
        db.commit()

        terminal_type = (
            "run_completed" if inquiry.status == "completed" else f"run_{inquiry.status}"
        )
        sink.emit(
            {
                "type": terminal_type,
                "supplier_count": len(suppliers),
                "elapsed_seconds": inquiry.total_elapsed_seconds,
            }
        )
        return _state.to_state(db, inquiry)
    except (BadRequest, NotFound):
        raise
    except Exception as exc:
        db.rollback()
        if claimed_inquiry_id is not None:
            failed = db.get(Inquiry, claimed_inquiry_id)
            if failed is not None:
                now = datetime.utcnow()
                failed.error_message = str(exc) or exc.__class__.__name__
                failed.heartbeat_at = now
                if failed.run_attempts < failed.max_attempts:
                    failed.status = "pending"
                    failed.next_retry_at = now + timedelta(
                        seconds=min(60, 2 ** max(failed.run_attempts, 1))
                    )
                else:
                    failed.status = "error"
                    failed.completed_at = now
                    failed.next_retry_at = None
                db.commit()
        raise
    finally:
        db.close()


def get_anchor_order_id_for_group(db: Session, group_id: int) -> int | None:
    """Return the order_id used as anchor by `run_inquiry_for_group`.

    The frontend needs this to drive the inquiry stream / cancel /
    preview endpoints (which are all per-order) when the user clicked
    "generate merged inquiry" from a group."""
    row = (
        db.query(Order.id)
        .filter(Order.group_id == group_id)
        .order_by(Order.created_at.asc())
        .first()
    )
    return row[0] if row else None


def run_inquiry_for_supplier(
    order_id: int,
    supplier_id: int,
    *,
    sink: InquiryProgressSink | None = None,
    template_id: int | None = None,
) -> InquiryState:
    """Re-run a single supplier without touching others."""
    sink = sink or NullSink()
    db = session_module.SessionLocal()
    try:
        order = _load_order(db, order_id)
        inquiry = repo.get_inquiry_by_order(db, order_id)
        source_results = (
            inquiry.match_snapshot
            if inquiry is not None and inquiry.group_id is not None
            else order.match_results
        ) or []
        products = [r for r in source_results if _supplier_id_of(r) == supplier_id]
        if not products:
            raise BadRequest(f"供应商 {supplier_id} 在订单 {order_id} 中没有匹配的产品")

        inquiry = inquiry or _state.ensure_inquiry(db, order_id=order_id)
        if inquiry.status not in ("in_progress",):
            inquiry.status = "in_progress"
            inquiry.started_at = inquiry.started_at or datetime.utcnow()
            inquiry.completed_at = None
        db.commit()
        inquiry_id = inquiry.id

        metadata = _order_metadata(order, db=db, supplier_id=supplier_id)
        if inquiry.group_id is not None:
            from domains.orders import service as orders_service

            group_meta = orders_service.find_order_group_meta(db, inquiry.group_id)
            if group_meta:
                if group_meta["ship_name"]:
                    metadata["ship_name"] = group_meta["ship_name"]
                if group_meta["loading_date"]:
                    metadata["loading_date"] = group_meta["loading_date"]
            po_numbers = [
                member.get("po_number")
                for member in inquiry.member_snapshot or []
                if member.get("po_number")
            ]
            if po_numbers:
                metadata["po_number"] = " / ".join(po_numbers)

        _supplier_worker.run_supplier_safe(
            inquiry_id=inquiry_id,
            supplier_id=supplier_id,
            products=products,
            metadata=metadata,
            sink=sink,
            template_id_override=template_id,
        )

        db.refresh(inquiry)
        suppliers = repo.list_inquiry_suppliers(db, inquiry_id)
        statuses = {s.status for s in suppliers}
        # Inquiry-level status after a SINGLE-supplier run completes:
        # The key distinction is `generating` (real worker in flight) vs
        # `pending` (user just hasn't clicked yet). Previously we treated
        # `pending` as in-progress, which left the inquiry spinning forever
        # when the user only generated 2 of 4 suppliers.
        if "generating" in statuses:
            # Some bulk-run worker still active — stay in_progress so its
            # finalize step gets to set the real terminal status.
            inquiry.status = "in_progress"
        elif "error" in statuses:
            inquiry.status = "error"
            inquiry.completed_at = inquiry.completed_at or datetime.utcnow()
        elif "completed" in statuses:
            # ≥1 supplier succeeded, no one running. Mark completed. The
            # user can still trigger pending suppliers later — that will
            # flip inquiry.status back to in_progress while the new worker
            # runs, then back to completed when done.
            inquiry.status = "partial" if inquiry.unmatched_items else "completed"
            inquiry.completed_at = inquiry.completed_at or datetime.utcnow()
        else:
            inquiry.status = "pending"
        db.commit()
        return _state.to_state(db, inquiry)
    finally:
        db.close()


def request_cancel(db: Session, order_id: int) -> Inquiry:
    """Cancel the inquiry — fully terminate so the UI stops spinning.

    Three cases:
    1. Any supplier is currently `generating` → set cancel_requested_at;
       the running worker observes it on its next checkpoint and the
       orchestrator (bulk path) finalises. Pending suppliers get marked
       `cancelled` so they won't ever be picked up.
    2. No suppliers running, some pending (single-supplier path: user
       generated 2 of 4 then clicked cancel) → mark pending as `cancelled`,
       set inquiry.status to terminal based on what succeeded.
    3. All suppliers already terminal → just stamp cancel_requested_at.
    """
    inquiry = repo.get_inquiry_by_order(db, order_id)
    if inquiry is None:
        raise NotFound("询价尚未启动")

    now = datetime.utcnow()
    inquiry.cancel_requested_at = now

    suppliers = repo.list_inquiry_suppliers(db, inquiry.id)

    # Pending suppliers will never be picked up after cancel — mark them.
    for s in suppliers:
        if s.status == "pending":
            s.status = "cancelled"
            s.completed_at = now

    # Re-read terminal counts after marking pending → cancelled.
    terminal_statuses = {"completed", "error", "cancelled"}
    in_flight = [s for s in suppliers if s.status not in terminal_statuses]

    if in_flight:
        # Some worker is genuinely running — let it finish, orchestrator path
        # will set final inquiry.status. Don't touch it here.
        pass
    else:
        # Everything settled. Pick the inquiry-level status:
        #   - any error → "error"
        #   - else any completed → "completed"
        #   - else (all cancelled or empty) → "cancelled"
        statuses = {s.status for s in suppliers}
        if "error" in statuses:
            inquiry.status = "error"
        elif "completed" in statuses:
            inquiry.status = "completed"
        else:
            inquiry.status = "cancelled"
        inquiry.completed_at = inquiry.completed_at or now

    db.commit()
    db.refresh(inquiry)
    return inquiry


# ─── Helpers ──────────────────────────────────────────────────


def _load_order(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise NotFound(f"订单 {order_id} 不存在")
    return order


def _supplier_id_of(match_result: dict[str, Any]) -> int | None:
    matched = match_result.get("matched_product") or {}
    sid = matched.get("supplier_id")
    if isinstance(sid, int):
        return sid
    return None


def _inquiry_row_issue(result: dict[str, Any]) -> str | None:
    """Return only safety issues that make this one RFQ row unusable."""
    if result.get("inquiry_exclusion_code"):
        return str(
            result.get("inquiry_exclusion_reason")
            or result["inquiry_exclusion_code"]
        )
    try:
        quantity = Decimal(str(result.get("quantity")))
        if not quantity.is_finite() or quantity <= 0:
            return "数量必须是大于 0 的数字"
    except (InvalidOperation, TypeError, ValueError):
        return "数量必须是大于 0 的数字"
    matched = result.get("matched_product") or {}
    source_unit = str(result.get("source_unit") or result.get("unit") or "").strip()
    supplier_unit = str(result.get("rfq_unit") or matched.get("unit") or "").strip()
    if not source_unit or not supplier_unit:
        return "订购单位或供应商单位缺失"
    if source_unit.upper() != supplier_unit.upper():
        evidence = result.get("conversion_evidence")
        if not isinstance(evidence, dict) or evidence.get("verified") is not True:
            return f"订购单位 {source_unit} 与供应商单位 {supplier_unit} 不一致，需确认换算"
    return None


def _member_snapshot(order: Order) -> dict[str, Any]:
    """Stable primitive-only identity for one arrangement member."""
    return {
        "order_id": order.id,
        "po_number": order.po_number or (order.order_metadata or {}).get("po_number"),
        "document_id": order.document_id,
        "filename": order.filename,
        "loading_date": order.loading_date,
        "port_id": order.port_id,
        "product_count": len(order.products or []),
    }


def _group_products_by_supplier(order: Order) -> dict[int, list[dict[str, Any]]]:
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in order.match_results or []:
        sid = _supplier_id_of(r)
        if sid is not None:
            groups[sid].append(r)
    return dict(groups)


def _order_metadata(
    order: Order,
    *,
    db: Session | None = None,
    supplier_id: int | None = None,
) -> dict[str, Any]:
    """Build the metadata dict the template engine expects.

    Precedence (highest first):
      1. `order.order_metadata.supplier_overrides[supplier_id]` (per-supplier user fill)
      2. `order.order_metadata` JSON (per-order user fill / LLM extract)
      3. Order column overrides (po_number/ship_name/.../destination_port)
      4. Port master (`Port.location` → `delivery_address`)
      5. Supplier master (`Supplier.address/phone/...` → `supplier_*` fields)

    Supplier-level keys (see `field_inspection.SUPPLIER_LEVEL_KEYS`) **only**
    read from layer 1; layer 2 is skipped for them to prevent cross-supplier
    contamination (a `supplier_tel` typed for supplier A would otherwise leak
    into supplier B's inquiry). Master-data injection only triggers when
    `db` is supplied.
    """
    from domains.inquiry.field_inspection import (
        SUPPLIER_LEVEL_KEYS,
        supplier_overrides_for,
    )

    raw = order.order_metadata or {}
    # Layer 2: top-level metadata, with supplier-level keys stripped (those
    # only legitimately live under supplier_overrides).
    base: dict[str, Any] = {
        k: v
        for k, v in raw.items()
        if k != "supplier_overrides" and k not in SUPPLIER_LEVEL_KEYS
    }
    column_overrides = {
        "po_number": order.po_number,
        "ship_name": order.ship_name,
        "vendor_name": order.vendor_name,
        "order_date": order.order_date,
        "delivery_date": order.delivery_date,
        "loading_date": order.loading_date,
        "invoice_number": order.invoice_number,
        "currency": order.currency,
        "destination_port": order.destination_port,
    }
    for k, v in column_overrides.items():
        if v not in (None, ""):
            base[k] = v

    # Layer 1: per-supplier overrides win over everything for supplier-level keys.
    for k, v in supplier_overrides_for(raw, supplier_id).items():
        if v not in (None, ""):
            base[k] = v

    if db is None:
        ship_name = str(base.get("ship_name") or "").strip()
        destination_port = str(base.get("destination_port") or "").strip()
        base["ship_port_title"] = (
            f"{ship_name}［{destination_port}］" if destination_port else ship_name
        )
        return base

    # TD-1 — Port + Supplier reads via masterdata public service so this
    # module no longer reaches into masterdata.models. Both helpers return
    # dicts (or None); attribute access becomes dict lookup.
    from domains.masterdata import service as masterdata_service

    # Port-level defaults
    if order.port_id is not None and not base.get("delivery_address"):
        port = masterdata_service.get_port(db, order.port_id)
        if port is not None and port.get("location"):
            base["delivery_address"] = port["location"]

    # Supplier-level defaults
    if supplier_id is not None:
        supplier = masterdata_service.get_supplier(db, supplier_id)
        if supplier is not None:
            supplier_field_defaults: dict[str, Any] = {
                "supplier_name": supplier.get("name"),
                "supplier_address": supplier.get("address"),
                "supplier_tel": supplier.get("phone"),
                "supplier_fax": supplier.get("fax"),
                "supplier_zip_code": supplier.get("zip_code"),
                "supplier_email": supplier.get("email"),
                "supplier_contact": supplier.get("contact"),
                "payment_method": supplier.get("default_payment_method"),
                "payment_date": supplier.get("default_payment_terms"),
            }
            for key, value in supplier_field_defaults.items():
                if value not in (None, "") and not base.get(key):
                    base[key] = value

    ship_name = str(base.get("ship_name") or "").strip()
    destination_port = str(base.get("destination_port") or "").strip()
    base["ship_port_title"] = (
        f"{ship_name}［{destination_port}］" if destination_port else ship_name
    )
    return base


__all__ = [
    "pre_analyze",
    "queue_inquiry_for_group",
    "run_inquiry",
    "run_inquiry_for_group",
    "run_inquiry_for_supplier",
    "request_cancel",
    "InquirySupplier",
]
