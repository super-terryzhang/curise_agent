"""Cancellation / graceful-shutdown primitive.

Distilled from hermes-agent/tools/interrupt.py (28 LOC) — but scoped
per-Agent instead of a global singleton, so two Agents in the same
process don't interfere.

Contract:
    - Agent constructs a `CancelToken` and owns it.
    - Agent.run() checks `token.is_set()` at every loop-boundary.
    - Any thread can call `agent.cancel()` / `token.set()` to request stop.
    - Tool handlers that do long work (web_fetch, bash, delegate_task)
      can check `ctx.cancelled()` to bail mid-work.

Triggers:
    - `Agent.cancel()` direct method call
    - SIGINT (Ctrl+C) via `install_sigint_handler(agent)` helper
    - Propagation from parent to subagent during delegation

When run() detects cancellation it:
    1. Stops spawning new LLM calls / tool calls
    2. Persists whatever state is in `ctx.messages` to the session DB
    3. Returns a string starting with `[cancelled]` so the caller can
       distinguish from a normal finish
"""

from __future__ import annotations

import signal
import threading
from typing import Any


class CancelToken:
    """Thread-safe cancel flag. Cheap to create, cheap to poll."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason: str = ""

    def set(self, reason: str = "user requested cancel") -> None:
        self._reason = reason
        self._event.set()

    def clear(self) -> None:
        self._reason = ""
        self._event.clear()

    def is_set(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    def raise_if_set(self) -> None:
        """Raise `CancelledError` if a cancel has been requested.

        Handy inside tool handlers / loop checks where you want to unwind.
        """
        if self._event.is_set():
            raise CancelledError(self._reason or "cancelled")


class CancelledError(Exception):
    """Raised by CancelToken.raise_if_set() when cancellation is requested."""


def install_sigint_handler(agent: Any) -> Any:
    """Install a SIGINT (Ctrl+C) handler that cancels the given Agent.

    Returns the previous handler so the caller can restore it later:

        prev = install_sigint_handler(agent)
        try:
            agent.run(task)
        finally:
            signal.signal(signal.SIGINT, prev)

    First Ctrl+C requests a clean cancel. A SECOND Ctrl+C restores the
    default handler so the user can force-kill if the graceful stop hangs.
    """
    state = {"count": 0}

    def _handler(signum, frame):  # noqa: ARG001
        state["count"] += 1
        if state["count"] == 1:
            try:
                agent.cancel("SIGINT — stopping gracefully; press Ctrl+C again to force")
            except Exception:
                pass
        else:
            # Second Ctrl+C: restore default and let Python re-raise.
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            raise KeyboardInterrupt()

    try:
        return signal.signal(signal.SIGINT, _handler)
    except (ValueError, OSError):
        # signal.signal only works on the main thread — fail silently in threads.
        return None
