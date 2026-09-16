"""Section 5 — Inquiry: progress sink fan-out + thread → asyncio bridging.

测试目标：
    sinks.py 是 orchestrator 把进度事件推给 HTTP SSE 流的桥。它必须：
    - NullSink 是真 no-op（任何调用都不能抛）。
    - RecordingSink 完整保留事件顺序（测试断言基础设施）。
    - CompositeSink 多 sink fan-out + cancel 用 OR 合并。
    - AsyncQueueSink 从 worker thread push → asyncio.Queue 不丢事件，
      loop 关掉之后 emit 不能 crash 整个 worker。

为什么重要：
    SSE 流挂掉用户看到的就是"询价卡在 0%"。worker thread 在 loop 死后崩溃
    会污染下一个请求的 thread pool（TestClient 在请求之间 tear down loop）。

设计方法：
    - 同步部分：直接构造 + emit + 读 events 列表。
    - asyncio 部分：在事件循环里跑一个 helper，用 to_thread / executor 在
      "另一个线程" 调 emit，断言 queue 收到事件；再关掉 loop 后再 emit。
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from domains.inquiry.sinks import (
    AsyncQueueSink,
    CancelEvent,
    CompositeSink,
    NullSink,
    RecordingSink,
)


# ─── NullSink ─────────────────────────────────────────────────


def test_null_sink_emit_is_a_noop():
    """No raise, no stored state — emit literally does nothing."""
    sink = NullSink()
    sink.emit({"type": "run_started"})
    sink.emit({"type": "supplier_done", "supplier_id": 1})
    # No public way to read events — confirm the methods exist & return None.
    assert sink.emit({"type": "x"}) is None


def test_null_sink_should_cancel_always_false():
    assert NullSink().should_cancel() is False


# ─── CancelEvent ──────────────────────────────────────────────


def test_cancel_event_starts_unset_then_can_be_requested():
    evt = CancelEvent()
    assert evt.is_set() is False
    evt.request()
    assert evt.is_set() is True


def test_cancel_event_request_is_idempotent():
    evt = CancelEvent()
    evt.request()
    evt.request()
    assert evt.is_set() is True


# ─── RecordingSink ────────────────────────────────────────────


def test_recording_sink_collects_events_in_order():
    sink = RecordingSink()
    sink.emit({"type": "run_started", "order_id": 1})
    sink.emit({"type": "supplier_start", "supplier_id": 100})
    sink.emit({"type": "supplier_done", "supplier_id": 100, "status": "completed"})

    assert len(sink.events) == 3
    assert sink.events[0]["type"] == "run_started"
    assert sink.events[1]["supplier_id"] == 100
    assert sink.events[2]["status"] == "completed"


def test_recording_sink_without_cancel_event_never_cancels():
    sink = RecordingSink()
    assert sink.should_cancel() is False


def test_recording_sink_cancel_reflects_underlying_event():
    evt = CancelEvent()
    sink = RecordingSink(cancel_event=evt)
    assert sink.should_cancel() is False
    evt.request()
    assert sink.should_cancel() is True


# ─── CompositeSink ────────────────────────────────────────────


def test_composite_sink_fans_out_to_all_delegates():
    """One emit → both child sinks receive the same event."""
    a = RecordingSink()
    b = RecordingSink()
    composite = CompositeSink([a, b])

    composite.emit({"type": "run_started"})
    composite.emit({"type": "supplier_done", "supplier_id": 7})

    assert [e["type"] for e in a.events] == ["run_started", "supplier_done"]
    assert [e["type"] for e in b.events] == ["run_started", "supplier_done"]


def test_composite_sink_cancel_is_or_across_delegates():
    """If any one child votes cancel, the composite cancels."""
    evt_a = CancelEvent()
    evt_b = CancelEvent()
    composite = CompositeSink([RecordingSink(evt_a), RecordingSink(evt_b)])
    assert composite.should_cancel() is False
    evt_b.request()
    assert composite.should_cancel() is True


def test_composite_sink_empty_delegates_does_not_crash():
    composite = CompositeSink([])
    composite.emit({"type": "noop"})
    assert composite.should_cancel() is False


# ─── AsyncQueueSink — cross-thread event bridge ───────────────


def test_async_queue_sink_push_from_worker_thread_reaches_loop_queue():
    """Worker thread emits → asyncio.Queue in the event loop receives the event."""

    async def scenario():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        cancel = CancelEvent()
        sink = AsyncQueueSink(queue, loop, cancel)

        def emit_from_thread():
            # `emit` runs OFF the event loop — simulates ThreadPoolExecutor worker.
            sink.emit({"type": "supplier_start", "supplier_id": 100})
            sink.emit({"type": "supplier_done", "supplier_id": 100})

        t = threading.Thread(target=emit_from_thread)
        t.start()
        # Let call_soon_threadsafe deliver into the queue, then drain.
        first = await asyncio.wait_for(queue.get(), timeout=2.0)
        second = await asyncio.wait_for(queue.get(), timeout=2.0)
        t.join(timeout=2.0)
        return first, second

    first, second = asyncio.run(scenario())
    assert first == {"type": "supplier_start", "supplier_id": 100}
    assert second == {"type": "supplier_done", "supplier_id": 100}


def test_async_queue_sink_should_cancel_reflects_cancel_event():
    async def scenario():
        loop = asyncio.get_running_loop()
        cancel = CancelEvent()
        sink = AsyncQueueSink(asyncio.Queue(), loop, cancel)
        return sink, cancel

    sink, cancel = asyncio.run(scenario())
    assert sink.should_cancel() is False
    cancel.request()
    assert sink.should_cancel() is True


def test_async_queue_sink_emit_after_loop_closed_does_not_crash():
    """TestClient tears the loop down between requests — emit must drop silently."""
    loop = asyncio.new_event_loop()
    cancel = CancelEvent()
    queue: asyncio.Queue = asyncio.Queue()
    sink = AsyncQueueSink(queue, loop, cancel)

    loop.close()
    # If this crashed, every late worker would explode after request teardown.
    sink.emit({"type": "supplier_done", "supplier_id": 1})


def test_async_queue_sink_emit_after_loop_stopped_but_not_closed():
    """Even before close(), a stopped loop must not raise — emit either drops or
    enqueues; the contract is "never crash the worker"."""
    loop = asyncio.new_event_loop()
    queue: asyncio.Queue = asyncio.Queue()
    sink = AsyncQueueSink(queue, loop, CancelEvent())
    # No close — just never start it. emit() may queue or drop, both fine.
    try:
        sink.emit({"type": "supplier_done"})
    finally:
        loop.close()


# ─── Composite + AsyncQueue end-to-end ────────────────────────


# ─── Terminal-event contract with v3 frontend (regression: 2026-05) ─────
#
# Production hit "生成完成后 UI 还在转圈" because the SSE stream emitted
# `run_completed` (a backend convention) while the frontend parser branched
# on `done` (the older convention). Neither matched, so the parser fell
# through to onStep, the SSE handle closed, and onDone never fired — the
# "Generating..." spinner spun forever. These tests pin both sides of the
# contract so backend can't add a new terminal type without conscious sync.


def test_inquiry_streams_terminal_types_match_frontend_accept_set():
    """The frontend's SSE parser only treats a fixed set of `event.type`
    values as terminal. If backend emits anything outside that set the UI
    spinner never clears (see orders-api.ts streamInquiryProgress /
    streamInquiryProgressWithKey).
    """
    from apps.http import _inquiry_streams

    # Mirror of the frontend's terminal handling in `orders-api.ts`. Listed
    # here so the test fails loudly when either side adds a new name; the
    # fix is to update *both*, not to widen this set silently.
    FRONTEND_ACCEPTED_TERMINAL_TYPES = {
        # v3 names (current)
        "run_completed",
        "run_error",
        "run_cancelled",
        "run_idle",
        "run_replaced",
        # legacy v2 names — accepted as alias for safety during cutover
        "done",
        "error",
        "cancelled",
    }
    backend_terminal = _inquiry_streams._TERMINAL_TYPES
    leaked = backend_terminal - FRONTEND_ACCEPTED_TERMINAL_TYPES
    assert not leaked, (
        f"Backend emits terminal type(s) the frontend won't recognise: "
        f"{sorted(leaked)}. Add them to streamInquiryProgress in "
        f"orders-api.ts (and to this test) before merging."
    )


def test_composite_plus_recording_plus_queue_all_receive_event():
    """Production pattern: SSE queue + DB recorder fan-out via CompositeSink."""

    async def scenario():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        cancel = CancelEvent()
        queue_sink = AsyncQueueSink(queue, loop, cancel)
        recorder = RecordingSink()
        composite = CompositeSink([queue_sink, recorder])

        def emit_from_thread():
            composite.emit({"type": "run_started", "order_id": 1})

        t = threading.Thread(target=emit_from_thread)
        t.start()
        first = await asyncio.wait_for(queue.get(), timeout=2.0)
        t.join(timeout=2.0)
        return first, recorder

    first, recorder = asyncio.run(scenario())
    assert first["type"] == "run_started"
    assert len(recorder.events) == 1
    assert recorder.events[0]["type"] == "run_started"
