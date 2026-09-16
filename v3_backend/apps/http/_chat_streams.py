"""In-process registry of active chat streams.

Per-session asyncio.Queue + CancelEvent so the SSE handler can attach
to a chat run already kicked off by `POST /sessions/{id}/messages`.
Single-process only (mirrors `_inquiry_streams.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from dataclasses import dataclass, field
from typing import Any

_TERMINAL_TYPES = {"run_completed", "run_error", "run_cancelled"}


@dataclass
class ChatStreamHandle:
    session_id: str
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    loop: asyncio.AbstractEventLoop | None = None
    completed: bool = False
    cancel: threading.Event = field(default_factory=threading.Event)

    def emit(self, event: dict[str, Any]) -> None:
        if self.loop is None or self.loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            self.loop.call_soon_threadsafe(self.queue.put_nowait, event)

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop


_streams: dict[str, ChatStreamHandle] = {}
_lock = threading.Lock()


def register(session_id: str) -> ChatStreamHandle:
    with _lock:
        old = _streams.pop(session_id, None)
        if old is not None and old.loop is not None and not old.loop.is_closed():
            with contextlib.suppress(RuntimeError):
                old.loop.call_soon_threadsafe(
                    old.queue.put_nowait, {"type": "run_replaced", "session_id": session_id}
                )
        handle = ChatStreamHandle(session_id=session_id)
        _streams[session_id] = handle
        return handle


def get(session_id: str) -> ChatStreamHandle | None:
    with _lock:
        return _streams.get(session_id)


def cancel(session_id: str) -> bool:
    handle = get(session_id)
    if handle is None:
        return False
    handle.cancel.set()
    return True


def remove(session_id: str) -> None:
    with _lock:
        _streams.pop(session_id, None)


def is_terminal(event: dict[str, Any]) -> bool:
    return event.get("type") in _TERMINAL_TYPES
