"""In-process registry of active inquiry streams.

Tracks per-order asyncio.Queue + CancelEvent so the SSE handler can
discover the stream that was opened by `POST /generate-inquiry`.

This is the simplest possible coordination point — single-process only.
A multi-replica deployment would replace this with Redis pub/sub or similar.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from dataclasses import dataclass, field
from typing import Any

from domains.inquiry.sinks import AsyncQueueSink, CancelEvent

_TERMINAL_TYPES = {"run_completed", "run_cancelled", "run_error"}


@dataclass
class StreamHandle:
    order_id: int
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    cancel: CancelEvent = field(default_factory=CancelEvent)
    loop: asyncio.AbstractEventLoop | None = None
    completed: bool = False

    def make_sink(self) -> AsyncQueueSink:
        if self.loop is None:
            raise RuntimeError("StreamHandle.loop is not bound — call attach_loop() first")
        return AsyncQueueSink(self.queue, self.loop, self.cancel)

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop


_streams: dict[int, StreamHandle] = {}
_lock = threading.Lock()


def register(order_id: int) -> StreamHandle:
    """Create (or replace) the stream for an order. The previous one — if any —
    is finalized so its consumer drops out cleanly."""
    with _lock:
        old = _streams.pop(order_id, None)
        if old is not None and old.loop is not None and not old.loop.is_closed():
            # Loop may be torn down between is_closed() check and the call;
            # drop silently since the old consumer is gone too.
            with contextlib.suppress(RuntimeError):
                old.loop.call_soon_threadsafe(
                    old.queue.put_nowait, {"type": "run_replaced", "order_id": order_id}
                )
        handle = StreamHandle(order_id=order_id)
        _streams[order_id] = handle
        return handle


def get(order_id: int) -> StreamHandle | None:
    with _lock:
        return _streams.get(order_id)


def cancel(order_id: int) -> bool:
    """Set the cancel event so the running orchestrator stops between suppliers.
    Returns True if a stream was found, False otherwise."""
    handle = get(order_id)
    if handle is None:
        return False
    handle.cancel.request()
    return True


def remove(order_id: int) -> None:
    with _lock:
        _streams.pop(order_id, None)


def is_terminal(event: dict[str, Any]) -> bool:
    return event.get("type") in _TERMINAL_TYPES
