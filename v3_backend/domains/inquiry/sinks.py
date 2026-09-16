"""Progress sinks — decouple inquiry orchestration from delivery channels (ADR-0002).

The orchestrator emits events through a `InquiryProgressSink`. Concrete sinks:
- `NullSink`: no-op for tests.
- `AsyncQueueSink`: pushes events into an `asyncio.Queue` from a worker thread,
  consumed by the SSE HTTP handler.
- `CompositeSink`: fans out to multiple sinks (SSE + DB).
- `CancelEvent`: thread-safe cancel flag, used both by sinks and the worker.

Phase 6 will add an `AgentStreamSink` that pushes the same events into the
agent runtime's stream queue — without changing the orchestrator.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterable
from typing import Any, Protocol

InquiryEvent = dict[str, Any]
"""Event payload. Required key: `type` (str). Optional: `supplier_id`,
plus event-specific fields (status, supplier_name, product_count, ...)."""


class InquiryProgressSink(Protocol):
    def emit(self, event: InquiryEvent) -> None: ...
    def should_cancel(self) -> bool: ...


class CancelEvent:
    """Thread-safe cancel flag. Wraps `threading.Event`."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()


class NullSink:
    """No-op sink — for tests + when no observer is attached."""

    def emit(self, event: InquiryEvent) -> None:  # noqa: ARG002
        return

    def should_cancel(self) -> bool:
        return False


class AsyncQueueSink:
    """Bridges thread-pool workers → asyncio.Queue consumed by SSE handler.

    The orchestrator is sync (runs on a worker thread). The HTTP SSE endpoint
    is async. `loop.call_soon_threadsafe()` makes it safe to push from any
    thread without locking.
    """

    def __init__(
        self,
        queue: asyncio.Queue[InquiryEvent],
        loop: asyncio.AbstractEventLoop,
        cancel_event: CancelEvent,
    ) -> None:
        self._queue = queue
        self._loop = loop
        self._cancel = cancel_event

    def emit(self, event: InquiryEvent) -> None:
        if self._loop.is_closed():
            return
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event)
        except RuntimeError:
            # Loop closed between is_closed() check and the call (TestClient
            # tears the loop down between requests). Drop silently — the SSE
            # consumer is gone too.
            return

    def should_cancel(self) -> bool:
        return self._cancel.is_set()


class CompositeSink:
    """Fan-out events to multiple sinks; cancel returns True if any votes True."""

    def __init__(self, sinks: Iterable[InquiryProgressSink]) -> None:
        self._sinks: list[InquiryProgressSink] = list(sinks)

    def emit(self, event: InquiryEvent) -> None:
        for sink in self._sinks:
            sink.emit(event)

    def should_cancel(self) -> bool:
        return any(sink.should_cancel() for sink in self._sinks)


class RecordingSink:
    """Captures events in-memory — used by tests to assert orchestrator behavior."""

    def __init__(self, cancel_event: CancelEvent | None = None) -> None:
        self.events: list[InquiryEvent] = []
        self._cancel = cancel_event

    def emit(self, event: InquiryEvent) -> None:
        self.events.append(event)

    def should_cancel(self) -> bool:
        return self._cancel.is_set() if self._cancel is not None else False


__all__ = [
    "InquiryEvent",
    "InquiryProgressSink",
    "CancelEvent",
    "NullSink",
    "AsyncQueueSink",
    "CompositeSink",
    "RecordingSink",
]
