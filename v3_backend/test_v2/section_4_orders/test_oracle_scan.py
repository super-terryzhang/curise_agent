from datetime import datetime, timedelta

import pytest

from apps.jobs import oracle_scan as scan
from domains.orders.oracle_models import OraclePOImport, OracleScanRun
from infrastructure.oracle.adapter import identity


def record(number, date="2026-09-10T00:00:00Z"):
    return {
        "POHeaderId": number,
        "OrderNumber": "PO" + str(number),
        "Revision": 0,
        "StatusCode": "OPEN",
        "CreationDate": date,
        "LastUpdateDate": date,
    }


@pytest.fixture
def configured(monkeypatch, seed_user):
    monkeypatch.setenv("ORACLE_SCAN_ENABLED", "true")
    monkeypatch.setenv("ORACLE_SCAN_NOT_BEFORE", "2026-09-09T00:00:00Z")
    monkeypatch.setenv("ORACLE_SCAN_OWNER_ID", str(seed_user.id))
    monkeypatch.setenv(
        "ORACLE_SCAN_JOB",
        "projects/cruise-v3-prod/locations/asia-northeast1/jobs/cruise-v3-po-hourly",
    )
    return seed_user


def test_manual_clicks_dispatch_once(db, configured, monkeypatch):
    calls = []
    monkeypatch.setattr(scan, "dispatch_job", lambda: calls.append(True))
    first = scan.request_scan(db, configured.id)
    second = scan.request_scan(db, configured.id)
    assert first["id"] == second["id"]
    assert len(calls) == 1
    assert first["trigger"] == "manual"


def test_dispatch_failure_releases_reservation(db, configured, monkeypatch):
    def fail():
        raise RuntimeError("private error detail")

    monkeypatch.setattr(scan, "dispatch_job", fail)
    result = scan.request_scan(db, configured.id)
    assert result["status"] == "failed"
    assert result["error_code"] == "SCAN_DISPATCH_FAILED"
    assert db.query(OracleScanRun).one().active_key is None


def test_scan_downloads_new_once_holds_history_and_detects_revision(db, configured):
    new, old = record(1), record(2, "2026-01-01T00:00:00Z")

    class Client:
        def list_orders(self):
            return [old, new]

    calls = []

    def importer(r, **kwargs):
        calls.append(r["OrderNumber"])
        assert kwargs["allow_model"] is True
        with scan.sessions.SessionLocal() as session:
            source = OraclePOImport(
                **identity(r),
                po_number=r["OrderNumber"],
                source_record=r,
                user_id=configured.id,
                status="completed",
                stage="completed",
            )
            session.add(source)
            session.commit()
            return scan.result_of(source)

    first = scan.execute_scan(client=Client(), importer=importer)
    assert first["status"] == "completed_with_issues"
    assert first["items"][0]["status"] == "historical_pending"
    scan.execute_scan(client=Client(), importer=importer)
    assert calls == ["PO1"]
    new["Revision"] = 1
    third = scan.execute_scan(client=Client(), importer=importer)
    assert third["items"][1]["issues"][0]["code"] == "REVISION_REQUIRES_ADOPTION"
    assert calls == ["PO1"]


def test_worker_claims_manual_run_without_duplicate(db, configured):
    run, _ = scan.reserve_scan(db, trigger="manual", requested_by=configured.id)

    class Client:
        def list_orders(self):
            return []

    result = scan.execute_scan(client=Client())
    assert result["id"] == run.id and result["trigger"] == "manual"
    assert result["status"] == "completed"
    assert db.query(OracleScanRun).count() == 1


def test_live_worker_not_overlapped_and_stale_released(db, configured):
    run, _ = scan.reserve_scan(db, trigger="manual")
    run.status = "running"
    db.commit()
    assert scan.execute_scan()["status"] == "already_running"
    run.heartbeat_at = datetime.utcnow() - timedelta(minutes=46)
    db.commit()
    replacement, created = scan.reserve_scan(db, trigger="manual")
    assert created and replacement.id != run.id
    assert run.status == "failed"


def test_manual_api_requires_writer_and_status_requires_login(client):
    assert client.get("/api/oracle/scans").status_code == 401
    assert client.post("/api/oracle/scans").status_code == 401


def pending_history(db, *numbers):
    run = OracleScanRun(trigger="scheduled", status="completed", items=[
        {"po_number": number, "status": "historical_pending"} for number in numbers
    ])
    db.add(run)
    db.commit()
    return run


def test_single_import_only_adopts_selected_po_and_coalesces_clicks(db, configured, monkeypatch):
    pending_history(db, "PO1", "PO2")
    dispatched = []
    monkeypatch.setattr(scan, "dispatch_job", lambda: dispatched.append(True))
    first = scan.request_po_import(db, configured.id, "PO1")
    again = scan.request_po_import(db, configured.id, "PO1")
    assert first["id"] == again["id"] and len(dispatched) == 1
    assert first["requested_by"] == configured.id
    with pytest.raises(RuntimeError, match="SCAN_BUSY"):
        scan.request_po_import(db, configured.id, "PO2")

    class Client:
        def list_orders(self):
            return [record(1, "2026-01-01T00:00:00Z"), record(2, "2026-01-01T00:00:00Z"), record(3)]

    imported = []

    def importer(r, **kwargs):
        imported.append(r["OrderNumber"])
        assert kwargs["allow_model"] is True
        return {"status": "completed"}

    result = scan.execute_scan(client=Client(), importer=importer)
    assert imported == ["PO1"]
    assert result["trigger"] == "manual_import" and result["status"] == "completed"
    assert len(result["items"]) == 1
    db.expire_all()
    assert db.get(OracleScanRun, first["id"]).active_key is None


def test_single_import_rechecks_open_and_rejects_unknown(db, configured, monkeypatch):
    pending_history(db, "PO1")
    monkeypatch.setattr(scan, "dispatch_job", lambda: None)
    with pytest.raises(LookupError):
        scan.request_po_import(db, configured.id, "invented")
    scan.request_po_import(db, configured.id, "PO1")

    class Client:
        def list_orders(self):
            return [{**record(1), "StatusCode": "CLOSED"}]

    def importer(*args, **kwargs):
        pytest.fail("Closed PO must not be imported")

    result = scan.execute_scan(client=Client(), importer=importer)
    assert result["items"][0]["issues"] == [{"code": "PO_NO_LONGER_OPEN"}]
    assert result["status"] == "completed_with_issues"


def test_single_import_dispatch_failure_can_retry(db, configured, monkeypatch):
    pending_history(db, "PO1")

    def fail():
        raise RuntimeError("dispatch unavailable")

    monkeypatch.setattr(scan, "dispatch_job", fail)
    result = scan.request_po_import(db, configured.id, "PO1")
    assert result["status"] == "failed"
    assert result["items"][0]["status"] == "historical_pending"
    monkeypatch.setattr(scan, "dispatch_job", lambda: None)
    retry = scan.request_po_import(db, configured.id, "PO1")
    assert retry["id"] != result["id"] and retry["status"] == "queued"


def test_single_import_preserves_full_list_after_many_requests(db, configured, monkeypatch):
    baseline = pending_history(db, "PO1", "PO2")
    for _ in range(11):
        db.add(OracleScanRun(trigger="manual_import", status="completed", items=[
            {"po_number": "PO1", "status": "completed"}
        ]))
    db.commit()
    assert baseline.id in [r["id"] for r in scan.scan_status(db)["runs"]]
    monkeypatch.setattr(scan, "dispatch_job", lambda: None)
    assert scan.request_po_import(db, configured.id, "PO2")["status"] == "queued"


def test_single_import_api_auth_and_dispatch(client, db, configured, auth_tokens, monkeypatch):
    pending_history(db, "PO1")
    monkeypatch.setattr(scan, "dispatch_job", lambda: None)
    assert client.post("/api/oracle/imports/PO1").status_code == 401
    response = client.post("/api/oracle/imports/PO1", headers={
        "Authorization": "Bearer " + auth_tokens["access_token"]
    })
    assert response.status_code == 202
    assert response.json()["items"] == [{"po_number": "PO1", "status": "queued"}]
