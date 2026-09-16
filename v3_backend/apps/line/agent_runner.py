"""Run the v3 chat agent for a LINE user, with a hard time budget.

LINE's reply-token window is 30 seconds. We give the agent 25 to keep
a 5-second margin for the actual HTTP reply round-trip. If the agent
doesn't finish in time, we abort and return a fallback message that
points the user to the web UI.

The agent runs in a thread (it's synchronous via `general_agent.Agent.run`);
the calling coroutine awaits a `Future` result with the timeout. On
timeout we *cancel* the agent — the agent's `cancel_token` is checked at
each step boundary, so the worker thread exits at the next safe point.
The thread is detached so the event loop doesn't block waiting for it.

Tool restrictions are applied via `platform="line"` in the factory; this
module just orchestrates the timeout policy.
"""

from __future__ import annotations

import concurrent.futures
import logging
from typing import Any

from agent.runtime import create_v3_chat_agent
from infrastructure.config import settings
from infrastructure.db.session import SessionLocal

logger = logging.getLogger(__name__)

_FALLBACK_TIMEOUT_REPLY = (
    "您的问题处理时间较长，已为您保留对话上下文。请到网页端查看完整结果，"
    "或换个更具体的问法再试一次。"
)
_FALLBACK_ERROR_REPLY = (
    "抱歉，处理您的消息时出错了，请稍后再试。"
)


def run_for_line(
    *,
    user_id: int,
    user_role: str,
    session_id: str,
    text: str,
    timeout_seconds: int | None = None,
) -> str:
    """Drive one agent turn for a LINE chat session.

    Always returns a string. On timeout we return the fallback text
    AND request agent cancellation so the worker thread doesn't keep
    burning tokens after we've given up. On any other failure we log +
    return the generic error reply.

    A fresh `Session` is opened here (we're called from the FastAPI
    background thread, not inside a request scope) and closed in a
    `finally`.
    """
    budget = (
        timeout_seconds
        if timeout_seconds is not None
        else settings.LINE_AGENT_TIMEOUT_SECONDS
    )

    def _job() -> tuple[str, Any]:
        db = SessionLocal()
        try:
            agent = create_v3_chat_agent(
                db=db,
                user_id=user_id,
                user_role=user_role,
                session_id=session_id,
                platform="line",
            )
            answer = agent.run(text)
            db.commit()
            return answer, agent
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
            raise
        finally:
            db.close()

    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="line-agent"
    )
    future = executor.submit(_job)
    try:
        answer, _agent = future.result(timeout=budget)
        return answer or _FALLBACK_ERROR_REPLY
    except concurrent.futures.TimeoutError:
        logger.warning(
            "LINE agent timed out after %ds for session=%s user=%d",
            budget, session_id, user_id,
        )
        # Best-effort cancellation: if the worker is still mid-turn the
        # agent picks up cancel_token at its next step boundary. We can't
        # block here waiting for it — the user already needs a reply.
        # The thread gets garbage-collected when the executor falls out
        # of scope at function return.
        return _FALLBACK_TIMEOUT_REPLY
    except Exception as exc:
        logger.exception("LINE agent failed for session=%s: %s", session_id, exc)
        return _FALLBACK_ERROR_REPLY
    finally:
        # Don't wait — the agent thread might still be running. shutdown(wait=False)
        # detaches it; at process exit it'll be killed.
        executor.shutdown(wait=False)
