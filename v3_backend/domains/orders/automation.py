"""Automatic continuation from an extracted PO through inquiry + anomalies."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm.attributes import flag_modified

from domains.document import repository as document_repository
from domains.inquiry import queue_inquiry_for_group, run_inquiry_for_group
from domains.orders import anomaly, repository
from domains.orders.groups.automation import auto_group_order
from domains.orders.matching import run_matching
from domains.orders.models import Order
from domains.orders.projection import project_purchase_order
from infrastructure.db import session as session_module

logger = logging.getLogger(__name__)

STAGE_NAMES = {
    1: "获取 PO",
    2: "文件校验与保存",
    3: "内容提取",
    4: "建立订单",
    5: "产品匹配",
    6: "供船安排归组",
    7: "生成询价",
    8: "异常检测与人工队列",
}


def automatic_from_document(document_id: int) -> int | None:
    """Create/reuse an Order and automatically run stages 4 through 8."""
    with session_module.SessionLocal() as db:
        document = document_repository.get(db, document_id)
        if document is None or document.doc_type != "purchase_order":
            return None
        if document.status != "extracted":
            return None
        existing_order = repository.get_by_document_id(db, document.id)
        existing_trace = (document.extracted_data or {}).get("_automation_pipeline")
        if existing_order is not None and isinstance(existing_trace, list):
            final = next(
                (row for row in existing_trace if row.get("step") == 8), None
            )
            if final and final.get("status") not in {"pending", "running"}:
                return existing_order.id
        trace = _initial_trace(document)
        _mark(trace, 4, "running")
        _save_document_trace(db, document, trace)
        try:
            order = existing_order
            if order is None:
                order = project_purchase_order(document, db, run_match_inline=False)
            _mark(
                trace,
                4,
                "completed",
                evidence={"document_id": document.id, "order_id": order.id},
            )
            _save_document_trace(db, document, trace)
        except Exception as exc:
            logger.exception("automatic PO projection failed for document %s", document_id)
            _mark(
                trace,
                4,
                "failed",
                message=f"建立订单失败：{exc}",
                error_code="ORDER_PROJECTION_FAILED",
                suggestion="检查提取结果后重新处理文档",
            )
            document.processing_error = f"自动建单失败：{exc}"
            _save_document_trace(db, document, trace)
            return None
        order_id = order.id
    automatic_order_pipeline(order_id, trace=trace)
    return order_id


def automatic_order_pipeline(
    order_id: int, *, trace: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Run matching, grouping, inquiry and anomaly detection without user gates."""
    with session_module.SessionLocal() as db:
        order = db.get(Order, order_id)
        if order is None:
            raise ValueError(f"订单 #{order_id} 不存在")
        pipeline = trace or _trace_from_order(order)
        inquiry_state = None
        current_step = 5
        try:
            _mark(pipeline, 5, "running")
            _save_order_trace(db, order, pipeline)
            match_result = run_matching(order, db)
            stats = match_result["statistics"]
            _mark(
                pipeline,
                5,
                "completed" if stats["not_matched"] == 0 else "completed_with_anomalies",
                evidence=stats,
            )
            order.status = "ready"
            order.processing_error = None
            repository.save(db, order)

            current_step = 6
            _mark(pipeline, 6, "running")
            _save_order_trace(db, order, pipeline)
            classification = auto_group_order(db, order.id)
            if classification is None:
                classification = {
                    "reason": "自动归组失败，订单已进入未分类，请人工选择供船安排"
                }
            db.refresh(order)
            if order.group_id is None:
                reason = classification.get("reason") or "缺少装船日或唯一目标港口"
                _mark(
                    pipeline,
                    6,
                    "needs_review",
                    message=reason,
                    error_code="ARRANGEMENT_REQUIRED",
                    suggestion="补充装船日和目标港口后重新自动归组",
                    evidence={"order_id": order.id},
                )
                _mark(pipeline, 7, "skipped", message="等待供船安排归组")
            else:
                _mark(
                    pipeline,
                    6,
                    "completed",
                    evidence={"group_id": order.group_id},
                )
                current_step = 7
                _mark(pipeline, 7, "running")
                _save_order_trace(db, order, pipeline)
                queued = queue_inquiry_for_group(db, order.group_id)
                inquiry_state = run_inquiry_for_group(
                    order.group_id,
                    inquiry_id=queued.id,
                )
                inquiry_status = inquiry_state.status
                _mark(
                    pipeline,
                    7,
                    "completed"
                    if inquiry_status == "completed"
                    else "completed_with_anomalies",
                    message=None
                    if inquiry_status == "completed"
                    else "部分商品或供应商需要人工处理",
                    evidence={
                        "group_id": order.group_id,
                        "inquiry_id": inquiry_state.id,
                        "version": inquiry_state.version,
                        "status": inquiry_status,
                        "supplier_count": inquiry_state.supplier_count,
                        "unassigned_count": inquiry_state.unassigned_count,
                    },
                )

            current_step = 8
            _mark(pipeline, 8, "running")
            result = anomaly.run_anomaly_check(
                order,
                inquiry=inquiry_state,
                pipeline=pipeline,
            )
            final_status = (
                "needs_review"
                if result["requires_human_review"]
                else "completed_with_warnings"
                if result["warning_count"]
                else "completed"
            )
            _mark(
                pipeline,
                8,
                final_status,
                evidence={
                    "total": result["total_anomalies"],
                    "warning": result["warning_count"],
                    "error": result["error_count"],
                    "blocking": result["blocking_count"],
                },
            )
            result["pipeline"] = pipeline
            order.anomaly_data = result
            flag_modified(order, "anomaly_data")
            repository.save(db, order)
            _mirror_document_trace(db, order, pipeline)
            return result
        except Exception as exc:
            logger.exception("automatic PO pipeline failed at step %s for order %s", current_step, order_id)
            _mark(
                pipeline,
                current_step,
                "failed",
                message=f"{STAGE_NAMES[current_step]}失败：{exc}",
                error_code=f"STEP_{current_step}_FAILED",
                suggestion="根据本阶段错误修复后重新运行",
            )
            _mark(pipeline, 8, "needs_review", message="自动流程中断，等待人工处理")
            order.processing_error = f"第 {current_step} 步失败：{exc}"
            order.status = "error"
            result = anomaly.run_anomaly_check(order, pipeline=pipeline)
            order.anomaly_data = result
            flag_modified(order, "anomaly_data")
            repository.save(db, order)
            _mirror_document_trace(db, order, pipeline)
            return result


def _initial_trace(document) -> list[dict[str, Any]]:
    existing = (document.extracted_data or {}).get("_automation_pipeline")
    if isinstance(existing, list):
        return existing
    trace = _blank_trace()
    _mark(trace, 1, "completed", evidence={"source": "manual_upload"})
    _mark(
        trace,
        2,
        "completed",
        evidence={
            "document_id": document.id,
            "filename": document.filename,
            "file_type": document.file_type,
            "file_size_bytes": document.file_size_bytes,
        },
    )
    _mark(
        trace,
        3,
        "completed",
        evidence={
            "document_id": document.id,
            "extraction_method": document.extraction_method,
            "product_count": len((document.extracted_data or {}).get("products") or []),
        },
    )
    return trace


def _trace_from_order(order: Order) -> list[dict[str, Any]]:
    extracted = order.extraction_data or {}
    existing = extracted.get("_automation_pipeline")
    if isinstance(existing, list):
        return existing
    trace = _blank_trace()
    for step in (1, 2, 3, 4):
        _mark(trace, step, "completed")
    return trace


def _blank_trace() -> list[dict[str, Any]]:
    return [
        {"step": step, "name": STAGE_NAMES[step], "status": "pending"}
        for step in range(1, 9)
    ]


def new_pipeline_trace() -> list[dict[str, Any]]:
    """Public constructor used by source-specific adapters such as Oracle."""
    return _blank_trace()


def mark_pipeline_stage(
    trace: list[dict[str, Any]],
    step: int,
    status: str,
    **details: Any,
) -> None:
    """Public stage recorder; adapters keep the same observable contract."""
    _mark(trace, step, status, **details)


def _mark(
    trace: list[dict[str, Any]],
    step: int,
    status: str,
    *,
    message: str | None = None,
    error_code: str | None = None,
    suggestion: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> None:
    row = next(item for item in trace if item["step"] == step)
    row.update(
        status=status,
        message=message,
        error_code=error_code,
        suggestion=suggestion,
        evidence=evidence or {},
        updated_at=datetime.utcnow().isoformat(),
    )


def _save_document_trace(db, document, trace: list[dict[str, Any]]) -> None:
    document.extracted_data = {
        **(document.extracted_data or {}),
        "_automation_pipeline": trace,
    }
    document_repository.save(db, document)


def _save_order_trace(db, order: Order, trace: list[dict[str, Any]]) -> None:
    order.anomaly_data = {**(order.anomaly_data or {}), "pipeline": trace}
    flag_modified(order, "anomaly_data")
    repository.save(db, order)


def _mirror_document_trace(db, order: Order, trace: list[dict[str, Any]]) -> None:
    if order.document_id is None:
        return
    document = document_repository.get(db, order.document_id)
    if document is not None:
        _save_document_trace(db, document, trace)


__all__ = [
    "STAGE_NAMES",
    "automatic_from_document",
    "automatic_order_pipeline",
    "mark_pipeline_stage",
    "new_pipeline_trace",
]
