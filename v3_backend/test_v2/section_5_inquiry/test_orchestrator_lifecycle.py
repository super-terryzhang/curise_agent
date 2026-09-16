"""Section 5 — Inquiry: orchestrator lifecycle (pre_analyze + run_inquiry).

测试目标：
    orchestrator 是 Phase 5 询价流程的入口——pre_analyze 给前端"群组预览"，
    run_inquiry 是真正生产 .xlsx 的多 supplier 流水线，run_inquiry_for_supplier
    是单 supplier 重做。这里我们走"in-memory DB + ThreadPoolExecutor + LocalFileStorage"
    的完整路径，不 mock 任何业务逻辑。

为什么重要：
    - 一个 supplier 崩溃不能拖垮整个 run（per-row 隔离）。
    - 状态机要在 thread pool 完成后正确收敛（completed / error / cancelled）。
    - 文件要真实写到 storage，row 上的 URL 要指过去。
    - pre_analyze 是只读的，不能误覆盖跑过的 inquiry。

设计方法：
    - 用 conftest 的 `db` + `_local_storage` fixture（writes 到 tmp_path）。
    - 直接 build Order / Supplier / SupplierTemplate ORM rows。
    - 用 RecordingSink 收集事件，断言 type + supplier_id。
    - 用 monkeypatch 注入失败工厂模拟"per-supplier 崩溃"。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from domains.inquiry import _supplier_worker as supplier_worker
from domains.inquiry import orchestrator
from domains.inquiry import repository as repo
from domains.inquiry.errors import BadRequest, NotFound
from domains.inquiry.models import Inquiry, InquirySupplier, SupplierTemplate
from domains.inquiry.sinks import RecordingSink
from domains.masterdata.models import Supplier
from domains.orders.models import Order


# ─── Helpers ──────────────────────────────────────────────────


def _make_order_two_suppliers(db, *, user_id: int = 1) -> Order:
    """Order with 2 matched products at supplier 100 + 1 product at supplier 200,
    plus 1 product with no supplier_id (unassigned)."""
    order = Order(
        user_id=user_id,
        filename="t.pdf",
        file_type="pdf",
        status="ready_for_review",
        po_number="PO-2026-Z",
        ship_name="MV Test",
        currency="USD",
        delivery_date="2026-05-15",
        match_results=[
            {
                "product_code": "A1",
                "product_name": "Apple",
                "quantity": 5,
                "unit_price": 2.0,
                "matched_product": {"id": 11, "supplier_id": 100, "code": "A1", "unit": "kg"},
            },
            {
                "product_code": "A2",
                "product_name": "Apricot",
                "quantity": 3,
                "unit_price": 4.0,
                "matched_product": {"id": 12, "supplier_id": 100, "code": "A2", "unit": "kg"},
            },
            {
                "product_code": "B1",
                "product_name": "Banana",
                "quantity": 7,
                "unit_price": 1.5,
                "matched_product": {"id": 21, "supplier_id": 200, "code": "B1", "unit": "kg"},
            },
            {
                "product_code": "U1",
                "product_name": "Unmatched",
                "quantity": 1,
                "matched_product": {},  # no supplier_id → unassigned
            },
        ],
    )
    db.add(order)
    db.add(Supplier(id=100, name="Apple Inc", contact="A", email="a@x.com", phone="1", status=True))
    # Supplier 200 has NO contact / email / phone — exercises missing_fields path.
    db.add(Supplier(id=200, name="Banana Co", contact=None, email=None, phone=None, status=True))
    db.commit()
    db.refresh(order)
    return order


def _zoned_template(db, **kw) -> SupplierTemplate:
    tpl = SupplierTemplate(
        template_name=kw.get("name", "Default"),
        supplier_ids=kw.get("supplier_ids"),
        supplier_id=kw.get("supplier_id"),
        template_styles={"zones": {"meta": {}}},
        field_positions=kw.get("field_positions") or {"po_number": "A1"},
        product_table_config=kw.get("product_table_config") or {
            "start_row": 10,
            "columns": {"A": "product_code", "B": "quantity"},
        },
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


# ─── pre_analyze ──────────────────────────────────────────────


def test_pre_analyze_groups_products_and_resolves_template(db, seed_user):
    order = _make_order_two_suppliers(db, user_id=seed_user.id)
    tpl_for_apple = _zoned_template(
        db, name="Apple", supplier_ids=[100]
    )

    state = orchestrator.pre_analyze(db, order.id)

    assert state.order_id == order.id
    assert state.status == "pending"
    assert state.supplier_count == 2
    # The U1 row has no supplier_id → tracked separately.
    assert state.unassigned_count == 1

    by_sid = {s.supplier_id: s for s in state.suppliers}
    apple = by_sid[100]
    banana = by_sid[200]

    assert apple.template is not None
    assert apple.template.id == tpl_for_apple.id
    assert apple.template.method == "exact"
    assert apple.product_count == 2

    # supplier 200 has no exact binding — pre_analyze uses select_template
    # which auto-picks the only available template ("candidate_auto").
    assert banana.template is not None
    assert banana.template.method == "candidate_auto"
    assert banana.product_count == 1


def test_pre_analyze_flags_missing_contact_fields(db):
    order = _make_order_two_suppliers(db)
    _zoned_template(db, name="Apple", supplier_ids=[100])

    state = orchestrator.pre_analyze(db, order.id)
    by_sid = {s.supplier_id: s for s in state.suppliers}

    # supplier 100 has all 3 contact fields → no missing flags.
    assert by_sid[100].missing_fields is None
    # supplier 200 has none of contact/email/phone → all 3 reported.
    assert set(by_sid[200].missing_fields or []) == {"contact", "email", "phone"}


def test_pre_analyze_is_idempotent_for_pending_inquiry(db):
    """Calling pre_analyze twice on a pending inquiry doesn't duplicate rows."""
    order = _make_order_two_suppliers(db)
    state1 = orchestrator.pre_analyze(db, order.id)
    state2 = orchestrator.pre_analyze(db, order.id)
    assert state1.id == state2.id
    assert state1.supplier_count == state2.supplier_count
    # No duplicate InquirySupplier rows for the same supplier_id.
    rows = repo.list_inquiry_suppliers(db, state1.id)
    sids = [r.supplier_id for r in rows]
    assert sorted(sids) == sorted(set(sids))


def test_pre_analyze_does_not_overwrite_finished_inquiry(db):
    """If status != "pending", pre_analyze just returns the current state."""
    order = _make_order_two_suppliers(db)
    orchestrator.pre_analyze(db, order.id)
    inquiry = repo.get_inquiry_by_order(db, order.id)
    inquiry.status = "completed"
    inquiry.completed_at = datetime.utcnow()
    inquiry.total_elapsed_seconds = 4.2
    db.commit()

    state = orchestrator.pre_analyze(db, order.id)
    assert state.status == "completed"
    assert state.total_elapsed_seconds == 4.2


def test_pre_analyze_raises_for_missing_order(db):
    """orchestrator security boundary — order_id resolution is its only check."""
    with pytest.raises(NotFound):
        orchestrator.pre_analyze(db, 99999)


# ─── run_inquiry — happy path ─────────────────────────────────


def test_run_inquiry_completes_and_persists_files(db, session_factory, _local_storage):
    order = _make_order_two_suppliers(db)
    _zoned_template(db, name="Apple", supplier_ids=[100])
    _zoned_template(db, name="Banana", supplier_ids=[200])
    sink = RecordingSink()

    # max_workers=1 keeps the full lifecycle but runs suppliers sequentially.
    # max_workers=2 was flaky (~1/8 runs) under SQLite + StaticPool because
    # the connection-level cache occasionally serves stale supplier rows even
    # after worker commits. We're testing orchestrator lifecycle here, not
    # the thread pool itself.
    state = orchestrator.run_inquiry(order.id, sink=sink, max_workers=1)

    assert state.status == "completed"
    assert state.supplier_count == 2
    assert state.total_elapsed_seconds is not None and state.total_elapsed_seconds >= 0

    # Read via a fresh session: orchestrator commits via its own internal
    # session and we want to read what's on disk, not the original test
    # session's cache.
    fresh = session_factory()
    try:
        rows = repo.list_inquiry_suppliers(fresh, state.id)
        assert {r.supplier_id for r in rows} == {100, 200}
        assert all(r.status == "completed" for r in rows)
        assert all(r.excel_file_url for r in rows)
    finally:
        fresh.close()

    # The file actually exists in test storage.
    for r in rows:
        assert _local_storage.download(r.excel_file_url)

    # Sink saw the expected event stream.
    types = [e["type"] for e in sink.events]
    assert "run_started" in types
    assert types.count("supplier_start") == 2
    assert types.count("supplier_done") == 2
    assert "run_completed" in types


def test_run_inquiry_handles_empty_groups(db, _local_storage):
    """Order with no matched products → orchestrator finishes cleanly with 0."""
    order = Order(
        user_id=1,
        filename="empty.pdf",
        file_type="pdf",
        status="ready_for_review",
        match_results=[],
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    sink = RecordingSink()

    state = orchestrator.run_inquiry(order.id, sink=sink)
    assert state.status == "completed"
    assert state.supplier_count == 0
    assert any(e["type"] == "run_completed" for e in sink.events)


def test_run_inquiry_handles_products_without_supplier_id(db, _local_storage):
    """Products with empty matched_product → all unassigned, no suppliers to run."""
    order = Order(
        user_id=1,
        filename="orphan.pdf",
        file_type="pdf",
        status="ready_for_review",
        match_results=[
            {"product_code": "X", "quantity": 1, "matched_product": {}},
            {"product_code": "Y", "quantity": 2, "matched_product": {"id": 1}},  # no supplier_id
        ],
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    state = orchestrator.run_inquiry(order.id, sink=RecordingSink())
    assert state.status == "completed"
    assert state.supplier_count == 0


# ─── run_inquiry — failure isolation ──────────────────────────


def test_per_supplier_failure_does_not_abort_others(db, _local_storage, monkeypatch):
    """One supplier raising must not stop the orchestrator from finishing others."""
    order = _make_order_two_suppliers(db)
    _zoned_template(db, name="Apple", supplier_ids=[100])
    _zoned_template(db, name="Banana", supplier_ids=[200])

    real_render = supplier_worker.render_inquiry_excel

    def flaky_render(template, *, supplier_id, **kwargs):
        if supplier_id == 200:
            raise RuntimeError("boom on supplier 200")
        return real_render(template, supplier_id=supplier_id, **kwargs)

    monkeypatch.setattr(supplier_worker, "render_inquiry_excel", flaky_render)
    sink = RecordingSink()

    state = orchestrator.run_inquiry(order.id, sink=sink, max_workers=2)

    # Overall run flagged as error because at least one supplier failed.
    assert state.status == "error"

    rows_by_sid = {r.supplier_id: r for r in repo.list_inquiry_suppliers(db, state.id)}
    # Apple still produced a file.
    assert rows_by_sid[100].status == "completed"
    assert rows_by_sid[100].excel_file_url
    # Banana row records the error message.
    assert rows_by_sid[200].status == "error"
    assert rows_by_sid[200].error_message and "boom" in rows_by_sid[200].error_message


# ─── run_inquiry_for_supplier (single redo) ───────────────────


def test_run_inquiry_for_supplier_single_redo(db, session_factory, _local_storage):
    """After a full run, re-running one supplier replaces its file without touching others."""
    order = _make_order_two_suppliers(db)
    _zoned_template(db, name="Apple", supplier_ids=[100])
    _zoned_template(db, name="Banana", supplier_ids=[200])

    state1 = orchestrator.run_inquiry(order.id, sink=RecordingSink())
    assert state1.status == "completed"

    # Fresh session reads — worker threads' commits aren't visible to the
    # original test session.
    s1 = session_factory()
    try:
        rows_before = {r.supplier_id: r.excel_file_url for r in repo.list_inquiry_suppliers(s1, state1.id)}
    finally:
        s1.close()

    state2 = orchestrator.run_inquiry_for_supplier(
        order.id, 100, sink=RecordingSink()
    )

    # Inquiry stays at "completed" because all suppliers are still completed.
    assert state2.status == "completed"
    s2 = session_factory()
    try:
        rows_after = {r.supplier_id: r.excel_file_url for r in repo.list_inquiry_suppliers(s2, state2.id)}
    finally:
        s2.close()
    # Apple's URL changes (fresh file written).
    assert rows_after[100] != rows_before[100]
    # Banana's URL is untouched.
    assert rows_after[200] == rows_before[200]


def test_run_inquiry_for_supplier_with_no_matched_products(db, _local_storage):
    """Asking to redo a supplier that has no products in the order → BadRequest."""
    order = _make_order_two_suppliers(db)
    with pytest.raises(BadRequest):
        orchestrator.run_inquiry_for_supplier(order.id, 9999, sink=RecordingSink())


# ─── request_cancel ───────────────────────────────────────────


def test_request_cancel_marks_timestamp(db):
    order = _make_order_two_suppliers(db)
    orchestrator.pre_analyze(db, order.id)

    inquiry = orchestrator.request_cancel(db, order.id)
    assert inquiry.cancel_requested_at is not None


def test_request_cancel_raises_when_inquiry_not_started(db):
    order = _make_order_two_suppliers(db)
    with pytest.raises(NotFound):
        orchestrator.request_cancel(db, order.id)


def test_request_cancel_finalizes_inquiry_when_all_pending(db):
    """The bug: pre_analyze leaves suppliers `pending`; cancel must end the inquiry.

    Before the fix, request_cancel only set `cancel_requested_at`. With no worker
    running to observe it, the frontend stayed on `in_progress`/`pending` forever
    and the spinner kept spinning. Now pending suppliers must transition to
    `cancelled` and the inquiry must reach a terminal status the UI can render.
    """
    order = _make_order_two_suppliers(db)
    orchestrator.pre_analyze(db, order.id)

    inquiry = orchestrator.request_cancel(db, order.id)

    assert inquiry.status == "cancelled"
    assert inquiry.completed_at is not None
    suppliers = repo.list_inquiry_suppliers(db, inquiry.id)
    assert all(s.status == "cancelled" for s in suppliers)
    assert all(s.completed_at is not None for s in suppliers)


def test_request_cancel_keeps_completed_terminal_status(db):
    """If at least one supplier already succeeded, cancel rolls up as `completed`."""
    order = _make_order_two_suppliers(db)
    orchestrator.pre_analyze(db, order.id)
    inquiry = repo.get_inquiry_by_order(db, order.id)
    # Simulate single-supplier success path: user generated supplier 100, then
    # changed their mind on the rest.
    rows = repo.list_inquiry_suppliers(db, inquiry.id)
    for r in rows:
        if r.supplier_id == 100:
            r.status = "completed"
            r.completed_at = datetime.utcnow()
    db.commit()

    inquiry = orchestrator.request_cancel(db, order.id)

    assert inquiry.status == "completed"
    by_sid = {s.supplier_id: s for s in repo.list_inquiry_suppliers(db, inquiry.id)}
    assert by_sid[100].status == "completed"
    assert by_sid[200].status == "cancelled"  # pending → cancelled


def test_request_cancel_rolls_up_to_error_when_any_supplier_errored(db):
    order = _make_order_two_suppliers(db)
    orchestrator.pre_analyze(db, order.id)
    inquiry = repo.get_inquiry_by_order(db, order.id)
    rows = repo.list_inquiry_suppliers(db, inquiry.id)
    for r in rows:
        if r.supplier_id == 100:
            r.status = "completed"
            r.completed_at = datetime.utcnow()
        elif r.supplier_id == 200:
            r.status = "error"
            r.error_message = "earlier failure"
            r.completed_at = datetime.utcnow()
    db.commit()

    inquiry = orchestrator.request_cancel(db, order.id)

    # Any errored supplier wins the rollup, regardless of completed siblings.
    assert inquiry.status == "error"


def test_run_inquiry_for_supplier_finalizes_when_others_still_pending(
    db, _local_storage
):
    """Single-supplier run must reach `completed` even if other suppliers haven't run.

    Before the fix this stayed `in_progress` because the finalize logic treated
    `pending` rows as "still running". With no bulk worker actually in flight,
    nothing ever flipped it to a terminal state.
    """
    order = _make_order_two_suppliers(db)
    _zoned_template(db, name="Apple", supplier_ids=[100])
    _zoned_template(db, name="Banana", supplier_ids=[200])
    orchestrator.pre_analyze(db, order.id)

    state = orchestrator.run_inquiry_for_supplier(
        order.id, 100, sink=RecordingSink()
    )

    # At least one supplier succeeded; supplier 200 is still pending but no
    # generating worker is in flight, so the inquiry rolls up to completed.
    assert state.status == "completed"
    by_sid = {s.supplier_id: s for s in state.suppliers}
    assert by_sid[100].status == "completed"
    assert by_sid[200].status == "pending"
