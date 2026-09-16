"""Durable dispatch and recovery for arrangement inquiry versions."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import and_, or_

from apps.jobs.runner import get_job_runner
from domains.inquiry.models import Inquiry
from domains.inquiry.orchestrator import run_inquiry_for_group
from infrastructure.db import session as session_module

logger = logging.getLogger(__name__)


def submit_group_inquiry(*, group_id: int, inquiry_id: int) -> str:
    """Submit a persisted inquiry version; duplicate local submits coalesce."""

    async def _run() -> None:
        await asyncio.to_thread(
            run_inquiry_for_group,
            group_id,
            inquiry_id=inquiry_id,
        )

    return get_job_runner().submit(
        _run,
        job_id=f"group-inquiry-{inquiry_id}",
    )


def resume_pending_group_inquiries(*, limit: int = 20) -> list[str]:
    """Re-dispatch due or stale persisted versions after a process restart.

    PostgreSQL ``SKIP LOCKED`` keeps several Cloud Run instances from selecting
    the same batch. The orchestrator also claims the inquiry row, so duplicate
    dispatch is harmless if two instances race between commit and submit.
    """
    now = datetime.utcnow()
    stale_before = now - timedelta(minutes=15)
    with session_module.SessionLocal() as db:
        rows = (
            db.query(Inquiry)
            .filter(
                Inquiry.group_id.is_not(None),
                or_(
                    and_(
                        Inquiry.status == "pending",
                        or_(
                            Inquiry.next_retry_at.is_(None),
                            Inquiry.next_retry_at <= now,
                        ),
                    ),
                    and_(
                        Inquiry.status == "in_progress",
                        or_(
                            Inquiry.heartbeat_at.is_(None),
                            Inquiry.heartbeat_at <= stale_before,
                        ),
                    ),
                ),
            )
            .order_by(Inquiry.id.asc())
            .with_for_update(skip_locked=True)
            .limit(limit)
            .all()
        )
        work: list[tuple[int, int]] = []
        for row in rows:
            if row.status == "in_progress":
                row.status = "pending"
                row.error_message = "上次执行中断，系统已自动恢复"
                row.next_retry_at = now
            work.append((row.group_id, row.id))
        db.commit()

    return [
        submit_group_inquiry(group_id=group_id, inquiry_id=inquiry_id)
        for group_id, inquiry_id in work
    ]


async def recovery_loop(*, interval_seconds: int = 30) -> None:
    """Periodically retry due persisted versions while an instance is alive."""
    while True:
        try:
            resume_pending_group_inquiries()
        except Exception:
            logger.exception("failed to recover pending arrangement inquiries")
        await asyncio.sleep(interval_seconds)


__all__ = ["recovery_loop", "resume_pending_group_inquiries", "submit_group_inquiry"]
