"""Shared durable scan state for hourly Cloud Run Jobs and manual triggers."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from apps.jobs.oracle_po import import_po, result_of
from domains.orders.oracle_models import OraclePOImport, OracleScanRun
from infrastructure.config import settings
from infrastructure.db import session as sessions
from infrastructure.oracle.adapter import OracleClient, identity


def configuration():
    return {
        "enabled": os.getenv("ORACLE_SCAN_ENABLED", "false").lower() == "true",
        "schedule": "0 * * * *",
        "timezone": "Asia/Tokyo",
        "not_before": os.getenv("ORACLE_SCAN_NOT_BEFORE", ""),
        "initial_pos": [
            p.strip() for p in os.getenv("ORACLE_SCAN_INITIAL_POS", "").split(",") if p.strip()
        ],
        "job_name": os.getenv("ORACLE_SCAN_JOB", ""),
    }


def serialize_run(run):
    result = {
        k: getattr(run, k)
        for k in (
            "id",
            "trigger",
            "requested_by",
            "status",
            "items",
            "error_code",
            "created_at",
            "started_at",
            "finished_at",
            "heartbeat_at",
        )
    }

    # Persist the short lifecycle state. The API adds the review summary from
    # item results; PostgreSQL's status column is VARCHAR(20).
    if run.status == "completed" and any(
        item.get("status") in ("failed", "needs_review", "historical_pending", "deferred")
        for item in (run.items or [])
    ):
        result["status"] = "completed_with_issues"
    return result


def reserve_scan(db, *, trigger, requested_by=None, items=None):
    now = datetime.utcnow()
    # A job has a 30-minute execution timeout. Expire abandoned reservations only
    # after 45 minutes; never overlap a live job to make the button feel faster.
    stale = (
        db.query(OracleScanRun)
        .filter(
            OracleScanRun.active_key == "oracle",
            OracleScanRun.heartbeat_at < now - timedelta(minutes=45),
        )
        .all()
    )
    for run in stale:
        run.active_key = None
        run.status = "failed"
        run.error_code = "SCAN_INTERRUPTED"
        run.finished_at = now
    db.commit()
    active = db.query(OracleScanRun).filter_by(active_key="oracle").first()
    if active:
        return active, False
    run = OracleScanRun(
        active_key="oracle",
        trigger=trigger,
        requested_by=requested_by,
        status="queued",
        items=items or [],
        heartbeat_at=now,
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.query(OracleScanRun).filter_by(active_key="oracle").one(), False
    return run, True


def dispatch_job():
    """Only dispatch a configured job; no credentials or business data in payload."""
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    name = configuration()["job_name"]
    if not name.startswith("projects/cruise-v3-prod/locations/asia-northeast1/jobs/cruise-v3-po-"):
        raise ValueError("ORACLE_SCAN_JOB_NOT_CONFIGURED")
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    with AuthorizedSession(credentials) as client:
        response = client.post(
            "https://run.googleapis.com/v2/" + name + ":run", json={}, timeout=20
        )
        response.raise_for_status()


def request_scan(db, user_id):
    config = configuration()
    if not config["enabled"] or not config["not_before"] or not config["job_name"]:
        raise ValueError("ORACLE_SCAN_NOT_ENABLED")
    run, created = reserve_scan(db, trigger="manual", requested_by=user_id)
    return _dispatch_reserved(db, run, created)


def request_po_import(db, user_id, po_number):
    config = configuration()
    if not config["enabled"] or not config["not_before"] or not config["job_name"]:
        raise ValueError("ORACLE_SCAN_NOT_ENABLED")
    # Only a PO actually observed in scan history can be explicitly adopted.
    runs = _recent_runs(db)
    observed = next(
        (item for run in runs for item in (run.items or [])
         if item.get("po_number") == po_number), None
    )
    active = db.query(OracleScanRun).filter_by(active_key="oracle").first()
    if active and active.trigger == "manual_import" and any(
        item.get("po_number") == po_number for item in (active.items or [])
    ):
        return serialize_run(active)
    if observed is None or observed.get("status") != "historical_pending":
        raise LookupError("PO_NOT_PENDING")
    run, created = reserve_scan(
        db, trigger="manual_import", requested_by=user_id,
        items=[{"po_number": po_number, "status": "queued"}],
    )
    if not created:
        if run.trigger == "manual_import" and any(
            item.get("po_number") == po_number for item in (run.items or [])
        ):
            return serialize_run(run)
        raise RuntimeError("SCAN_BUSY")
    return _dispatch_reserved(db, run, created)


def _dispatch_reserved(db, run, created):
    if created:
        try:
            dispatch_job()
        except Exception:
            run.status = "failed"
            run.active_key = None
            run.error_code = "SCAN_DISPATCH_FAILED"
            if run.trigger == "manual_import":
                run.items = [{**item, "status": "historical_pending"} for item in run.items]
            run.finished_at = datetime.utcnow()
            db.commit()
    return serialize_run(run)


def _recent_runs(db):
    runs = db.query(OracleScanRun).order_by(OracleScanRun.id.desc()).limit(10).all()
    baseline = (db.query(OracleScanRun)
                .filter(OracleScanRun.trigger.in_(["manual", "scheduled"]))
                .order_by(OracleScanRun.id.desc()).first())
    if baseline and all(r.id != baseline.id for r in runs):
        runs.append(baseline)
    return runs


def scan_status(db):
    config = configuration()
    runs = _recent_runs(db)
    sources = db.query(OraclePOImport).order_by(OraclePOImport.created_at.desc()).limit(100).all()
    now = datetime.now(UTC)
    next_scan = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return {
        **{k: config[k] for k in ("enabled", "schedule", "timezone", "not_before")},
        "next_scan_at": next_scan if config["enabled"] else None,
        "runs": [serialize_run(r) for r in runs],
        "files": [
            {
                **result_of(s),
                "downloaded_at": s.created_at if s.document_id else None,
                "updated_at": s.updated_at,
            }
            for s in sources
        ],
    }


def execute_scan(*, client=None, importer=import_po):
    config = configuration()
    if not config["enabled"]:
        return {"status": "disabled"}
    start = datetime.fromisoformat(config["not_before"].replace("Z", "+00:00"))
    if start.tzinfo is None:
        raise ValueError("ORACLE_SCAN_START_REQUIRES_TIMEZONE")
    owner = int(os.environ["ORACLE_SCAN_OWNER_ID"])
    folder = (
        int(os.environ["ORACLE_SCAN_FOLDER_ID"]) if os.getenv("ORACLE_SCAN_FOLDER_ID") else None
    )
    policy = json.loads(os.getenv("ORACLE_SCAN_POLICY_JSON", "{}"))
    with sessions.SessionLocal() as db:
        run, _ = reserve_scan(db, trigger="scheduled")
        claimed = (
            db.query(OracleScanRun)
            .filter_by(id=run.id, status="queued")
            .update(
                {
                    "status": "running",
                    "started_at": datetime.utcnow(),
                    "heartbeat_at": datetime.utcnow(),
                }
            )
        )
        db.commit()
        if not claimed:
            return {"status": "already_running", "run_id": run.id}
        db.refresh(run)
        target = (
            run.items[0]["po_number"]
            if run.trigger == "manual_import" and run.items else None
        )
        try:
            client = client or OracleClient(
                {"username": os.getenv("ORACLE_USERNAME"), "password": os.getenv("ORACLE_PASSWORD")}
            )
            records = [r for r in client.list_orders() if r.get("StatusCode") == "OPEN"]
            if target:
                records = [r for r in records if r["OrderNumber"] == target]
                run.items = []
                if not records:
                    run.items = [{"po_number": target, "status": "needs_review",
                                  "issues": [{"code": "PO_NO_LONGER_OPEN"}]}]
            records.sort(key=lambda r: r["CreationDate"])
            limit = max(1, min(100, int(os.getenv("ORACLE_SCAN_BATCH_SIZE", "20"))))
            imported = 0
            for record in records:
                keys = identity(record)
                source = db.get(OraclePOImport, keys["source_key"])
                row = {"po_number": record["OrderNumber"], "status": "pending"}
                created = datetime.fromisoformat(record["CreationDate"].replace("Z", "+00:00"))
                if source:
                    row.update(result_of(source))
                    if source.version_key != keys["version_key"]:
                        row.update(
                            status="needs_review", issues=[{"code": "REVISION_REQUIRES_ADOPTION"}]
                        )
                    elif source.status == "processing":
                        row.update(
                            status="needs_review",
                            issues=[{"code": "IMPORT_INTERRUPTED_REVIEW_REQUIRED"}],
                        )
                elif (created < start and record["OrderNumber"] not in config["initial_pos"]
                      and record["OrderNumber"] != target):
                    row.update(
                        status="historical_pending",
                        issues=[{"code": "HISTORICAL_OPEN_REQUIRES_ADOPTION"}],
                    )
                elif imported >= limit:
                    row["status"] = "deferred"
                else:
                    imported += 1
                    row.update(status="processing", stage="download")
                    run.items = [*(run.items or []), row]
                    run.heartbeat_at = datetime.utcnow()
                    db.commit()
                    result = importer(
                        record,
                        client=client,
                        user_id=owner,
                        folder_id=folder,
                        cache_dir=Path(
                            os.getenv("ORACLE_SCAN_CACHE_DIR", "/tmp/oracle-recognition")
                        ),
                        allow_model=True,
                        api_key=settings.GOOGLE_API_KEY,
                        unit_approvals=policy.get("unit_approvals", []),
                        template_overrides={
                            int(k): v for k, v in policy.get("template_overrides", {}).items()
                        },
                    )
                    row.update(result)
                    run.items = [*(run.items or [])[:-1], row]
                    run.heartbeat_at = datetime.utcnow()
                    db.commit()
                    continue
                run.items = [*(run.items or []), row]
                run.heartbeat_at = datetime.utcnow()
                db.commit()
            run.status = "completed"
        except Exception as error:
            db.rollback()
            run.status = "failed"
            run.error_code = "SCAN_" + type(error).__name__.upper()
        run.active_key = None
        run.finished_at = datetime.utcnow()
        db.commit()
        return serialize_run(run)
