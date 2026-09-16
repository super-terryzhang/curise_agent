"""Background job runner — abstraction over async task execution.

Current implementation: in-process `asyncio.create_task`. Future migration
to Celery/RQ only requires swapping the implementation (ADR-0005).

Usage:

    runner = get_job_runner()
    job_id = runner.submit(my_async_task, arg1, arg2, keyword=value)
    status = runner.status(job_id)
    runner.cancel(job_id)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

logger = logging.getLogger(__name__)


JobFn = Callable[..., Awaitable[None]]


class BackgroundJobRunner(Protocol):
    def submit(self, fn: JobFn, /, *args: Any, job_id: str | None = None, **kwargs: Any) -> str: ...

    def cancel(self, job_id: str) -> bool: ...

    def status(self, job_id: str) -> dict[str, Any]: ...


class AsyncioRunner:
    """In-process runner.

    - Uses `asyncio.create_task` when called from inside a running loop.
    - Falls back to a daemon thread when no loop is running (sync context).
    - No persistence; jobs are lost on process restart.
    - No retries (task failures are logged).
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def submit(
        self,
        fn: JobFn,
        /,
        *args: Any,
        job_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        import threading

        jid = job_id or uuid.uuid4().hex
        existing = self._tasks.get(jid)
        if existing is not None and not existing.done():
            return jid
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            task = loop.create_task(self._wrap(jid, fn, args, kwargs), name=f"job:{jid}")
            self._tasks[jid] = task
        else:
            # No running loop — run in a dedicated thread.
            thread = threading.Thread(
                target=lambda: asyncio.run(self._wrap(jid, fn, args, kwargs)),
                daemon=True,
                name=f"job:{jid}",
            )
            thread.start()
        return jid

    async def _wrap(
        self, jid: str, fn: JobFn, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> None:
        try:
            await fn(*args, **kwargs)
        except asyncio.CancelledError:
            logger.info("job %s cancelled", jid)
            raise
        except Exception:
            logger.exception("job %s failed", jid)
        finally:
            self._tasks.pop(jid, None)

    def cancel(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def status(self, job_id: str) -> dict[str, Any]:
        task = self._tasks.get(job_id)
        if task is None:
            return {"job_id": job_id, "state": "unknown"}
        if task.cancelled():
            return {"job_id": job_id, "state": "cancelled"}
        if task.done():
            exc = task.exception()
            return {
                "job_id": job_id,
                "state": "failed" if exc else "completed",
                "error": str(exc) if exc else None,
            }
        return {"job_id": job_id, "state": "running"}


class SynchronousRunner:
    """Runs jobs inline — used by tests that need deterministic completion.

    Works both inside and outside a running event loop:
    - Inside a loop (e.g. a FastAPI async endpoint during TestClient calls),
      runs the coroutine in a dedicated thread with its own event loop and
      joins before returning.
    - Outside any loop, uses `asyncio.run` directly.
    """

    def __init__(self) -> None:
        self._results: dict[str, dict[str, Any]] = {}

    def submit(
        self,
        fn: JobFn,
        /,
        *args: Any,
        job_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        import threading

        jid = job_id or uuid.uuid4().hex
        existing = self._results.get(jid)
        if existing is not None and existing.get("state") == "running":
            return jid
        self._results[jid] = {"job_id": jid, "state": "running", "error": None}

        try:
            asyncio.get_running_loop()
            in_loop = True
        except RuntimeError:
            in_loop = False

        error: list[BaseException] = []

        async def _runner_coro() -> None:
            await fn(*args, **kwargs)

        def _target() -> None:
            try:
                asyncio.run(_runner_coro())
            except BaseException as exc:
                error.append(exc)

        if in_loop:
            t = threading.Thread(target=_target, daemon=True)
            t.start()
            t.join()
        else:
            _target()

        if error:
            logger.warning("synchronous job %s failed: %s", jid, error[0])
            self._results[jid] = {"job_id": jid, "state": "failed", "error": str(error[0])}
        else:
            self._results[jid] = {"job_id": jid, "state": "completed", "error": None}
        return jid

    def cancel(self, job_id: str) -> bool:
        return False  # jobs complete before submit() returns

    def status(self, job_id: str) -> dict[str, Any]:
        return self._results.get(job_id, {"job_id": job_id, "state": "unknown"})


_runner: BackgroundJobRunner = AsyncioRunner()


def get_job_runner() -> BackgroundJobRunner:
    return _runner


def set_job_runner(runner: BackgroundJobRunner) -> None:
    """Inject a different runner (used for tests + future Celery migration)."""
    global _runner
    _runner = runner
