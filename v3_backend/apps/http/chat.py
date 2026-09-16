"""Chat HTTP — `/api/chat/*` for the AI assistant page.

Endpoints:
- GET    /models                    — list selectable LLM models
- POST   /sessions                  — create a session
- GET    /sessions                  — list user's sessions
- GET    /sessions/{id}             — fetch session + messages
- PATCH  /sessions/{id}             — update title / model
- DELETE /sessions/{id}             — delete a session (cascades messages)
- POST   /sessions/{id}/messages    — send a message; agent runs in bg, SSE streams
- GET    /sessions/{id}/stream      — SSE for live progress
- POST   /sessions/{id}/cancel      — abort the active run
- POST   /actions/{id}/decide       — approve / reject a HITL pending action

Auth: every endpoint requires a logged-in user. Cross-user access is
silently 404'd by `DBStorage`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from agent.runtime import create_v3_chat_agent

# Side-effect import: ensure tool modules register their @tool decorators
# AND their ACTION_DISPATCH entries before the /decide endpoint dispatches.
from agent.runtime import tools as _runtime_tools  # noqa: F401
from agent.runtime.approvals import (
    fetch_pending,
    get_spec,
    mark_decided,
)
from agent.runtime.tools.propose import emit_resolved
from agent.storage.models import ChatMessage, ChatSession
from apps.http import _chat_streams
from apps.http._deps import CurrentUser, DbDep, Writer
from infrastructure.jobs.runner import get_job_runner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


class _NewSessionBody(BaseModel):
    title: str = "新对话"
    # Optional per-session model (e.g. "kimi-k2", "gemini-2.5-flash").
    # NULL → settings default. Provider auto-selected from prefix.
    model: str | None = None


class _PatchSessionBody(BaseModel):
    title: str | None = None
    model: str | None = None


class _NewMessageBody(BaseModel):
    text: str
    # Where the user was when they sent this message — e.g.
    # "/dashboard/orders/123". Surfaced to the agent as a per-turn
    # overlay on the system prompt (see factory.create_v3_chat_agent
    # and _PAGE_CONTEXT_OVERLAY). Optional and intentionally a
    # free-form string: the frontend resolves URL + key params into
    # whatever phrasing reads well; backend just passes it through.
    page_context: str | None = None


class _DecideBody(BaseModel):
    decision: str = Field(..., pattern="^(approve|reject)$")
    notes: str = ""


# ─── Sessions CRUD ────────────────────────────────────────────


# Models the UI can offer in the per-session picker. Hardcoded so the
# frontend can render the list without hitting the LLM. Each entry must
# resolve to a real provider via `agent.runtime.llm._resolve_provider`;
# adding a new entry only requires the key env var to be set in prod.
_SUPPORTED_MODELS: list[dict[str, str]] = [
    {
        "id": "gemini-3.5-flash",
        "label": "Gemini 3.5 Flash",
        "provider": "gemini",
        "notes": "默认；2026-05-19 GA，准确度优于 2.5/3-preview",
    },
    {
        "id": "kimi-k2.6",
        "label": "Kimi K2.6",
        "provider": "moonshot",
        "notes": "Moonshot 国内站；256K 上下文，工具调用稳",
    },
    {
        "id": "kimi-k2.5",
        "label": "Kimi K2.5",
        "provider": "moonshot",
        "notes": "Moonshot 国内站；上一代 K2，备用",
    },
]


@router.get("/models")
def list_models() -> dict[str, Any]:
    """Models the user can pick for a session.

    Static list — provider routing is auto-derived from the model name
    prefix by `_resolve_provider`. Frontend reads this to populate the
    chat session model picker.
    """
    return {"models": _SUPPORTED_MODELS}


@router.post("/sessions")
def create_session(body: _NewSessionBody, db: DbDep, user: Writer) -> dict[str, Any]:
    row = ChatSession(
        id=_uuid(),
        user_id=user.id,
        title=body.title,
        model=body.model,
    )
    db.add(row)
    db.commit()
    return _session_to_dict(row)


@router.patch("/sessions/{session_id}")
def update_session(
    session_id: str,
    body: _PatchSessionBody,
    db: DbDep,
    user: Writer,
) -> dict[str, Any]:
    """Patch session metadata. Today only `title` and `model` are mutable.
    The model switch takes effect on the *next* `send_message` turn — an
    in-flight run keeps the model it was started with."""
    s = db.get(ChatSession, session_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")
    if body.title is not None:
        s.title = body.title
    if body.model is not None:
        # Empty string → clear back to default.
        s.model = body.model or None
    db.commit()
    db.refresh(s)
    return _session_to_dict(s)


@router.get("/sessions")
def list_sessions(db: DbDep, user: CurrentUser) -> list[dict[str, Any]]:
    stmt = (
        select(ChatSession)
        .where(ChatSession.user_id == user.id)
        .order_by(ChatSession.updated_at.desc())
    )
    return [_session_to_dict(s) for s in db.execute(stmt).scalars()]


@router.get("/sessions/{session_id}")
def get_session(session_id: str, db: DbDep, user: CurrentUser) -> dict[str, Any]:
    s = db.get(ChatSession, session_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")
    msgs = list(
        db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.sequence.asc())
        ).scalars()
    )
    return {
        **_session_to_dict(s),
        "messages": [_message_to_dict(m) for m in msgs],
        # Rehydrate artifact panels from persisted message history.
        # Prod 2026-05-20 bug: generic_table / upload_diff_viewer panels
        # disappeared on refresh because the SSE-only event delivery
        # path had no persistence-side recovery. The agent's
        # `present_artifact` tool_call already carries the full payload
        # in `tool_calls[i].function.arguments` — we just have to
        # surface it through the API. Both inline (generic_table) and
        # reference-based (upload_diff_viewer) artifacts work via this
        # single path; for the reference flavor the frontend re-fetches
        # the underlying data from the artifact REST endpoint as before.
        "artifacts": _extract_artifacts_from_messages(msgs, user_id=user.id),
    }


def _extract_artifacts_from_messages(
    msgs: list[ChatMessage], *, user_id: int
) -> list[dict[str, Any]]:
    """Walk persisted messages and rebuild the `artifacts[]` array the
    frontend's chat store would have built from live SSE events.

    Pairs each assistant `present_artifact` tool_call with its matching
    tool result row (by `tool_call_id`). Only artifacts whose dispatch
    succeeded (tool result is NOT an `Error:` prefix) get included —
    failed dispatches stay out of the panel queue so reload doesn't
    resurrect a broken artifact the user already moved past.
    """
    # First pass: collect tool result content keyed by tool_call_id.
    # The validator's success ack starts with "Artifact dispatched";
    # failures start with "Error:". We need to know which is which.
    tool_results_by_id: dict[str, str] = {}
    for m in msgs:
        if m.role != "tool":
            continue
        parts = m.parts or []
        if not parts:
            continue
        data = parts[0].get("data") or {}
        if not isinstance(data, dict):
            continue
        tc_id = data.get("tool_call_id")
        if not tc_id:
            continue
        tool_results_by_id[tc_id] = str(data.get("content") or "")

    # Second pass: scan assistant tool_calls in order; for every
    # present_artifact call whose paired tool result indicates success,
    # parse the arguments JSON and append to the output list.
    out: list[dict[str, Any]] = []
    seq = 0
    for m in msgs:
        if m.role != "assistant":
            continue
        parts = m.parts or []
        if not parts:
            continue
        payload = parts[0].get("data") or {}
        for tc in (payload.get("tool_calls") or []):
            fn = (tc or {}).get("function") or {}
            if fn.get("name") != "present_artifact":
                continue
            tc_id = tc.get("id")
            result_text = tool_results_by_id.get(tc_id or "", "")
            if result_text.startswith("Error:"):
                # Failed validation — frontend never showed a panel for
                # this call, so reload shouldn't either.
                continue
            args_raw = fn.get("arguments")
            if not args_raw:
                continue
            try:
                args_obj = (
                    args_raw if isinstance(args_raw, dict)
                    else json.loads(args_raw)
                )
            except (ValueError, json.JSONDecodeError):
                continue
            data_field = args_obj.get("data")
            if isinstance(data_field, str):
                try:
                    data_field = json.loads(data_field)
                except (ValueError, json.JSONDecodeError):
                    # Agent passed a string that isn't JSON. Leave as-is
                    # and let the frontend either render or skip — better
                    # than dropping a panel the user might still want.
                    pass
            seq += 1
            out.append({
                "id": seq,
                "session_id": m.session_id,
                "component": args_obj.get("component", ""),
                "data": data_field if data_field is not None else {},
                "narration": args_obj.get("narration", ""),
                "user_id": user_id,
            })

    # Dedup: agents (esp. on retry) sometimes dispatch the same artifact
    # multiple times — same component + same `data` payload. The UI then
    # shows N identical tabs which is pure noise. Collapse on
    # (component, JSON-stable data hash), keeping the LATEST occurrence
    # so any subsequent edit to the data is preserved. Narration is
    # intentionally NOT part of the dedup key — different narrations on
    # the same underlying data still collapse (typically just typo
    # variants from the agent).
    deduped: dict[str, dict[str, Any]] = {}
    for art in out:
        key = (
            f"{art['component']}::"
            + json.dumps(art["data"], sort_keys=True, ensure_ascii=False, default=str)
        )
        deduped[key] = art
    # Reassign ids so they stay sequential after dedup — the frontend
    # treats `id` as a stable React key, so gaps don't break correctness
    # but compact ids make the panel position counter ("3 / 5") sensible.
    final = list(deduped.values())
    for i, art in enumerate(final, start=1):
        art["id"] = i
    return final


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, db: DbDep, user: Writer) -> dict[str, Any]:
    s = db.get(ChatSession, session_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")
    db.delete(s)
    db.commit()
    _chat_streams.remove(session_id)
    return {"ok": True, "session_id": session_id}


# ─── Messages + SSE ───────────────────────────────────────────


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str, body: _NewMessageBody, db: DbDep, user: Writer
) -> dict[str, Any]:
    """Kick off an agent run on this session. Returns 202 immediately —
    clients should already have `GET /stream` open to observe progress."""
    s = db.get(ChatSession, session_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")

    handle = _chat_streams.register(session_id)
    handle.attach_loop(asyncio.get_running_loop())
    text = body.text
    session_model = s.model  # snapshot now; mid-run PATCH must not flip mid-flight
    page_context = body.page_context

    user_role = user.role
    user_id = user.id

    async def _job() -> None:
        try:
            handle.emit({"type": "run_started", "session_id": session_id})
            await asyncio.to_thread(
                _run_agent_blocking,
                session_id,
                user_id,
                user_role,
                text,
                session_model,
                page_context,
            )
            handle.emit({"type": "run_completed", "session_id": session_id})
        except Exception as exc:
            logger.exception("chat: session %s failed", session_id)
            handle.emit({"type": "run_error", "session_id": session_id, "error": str(exc)})
        finally:
            handle.completed = True

    job_id = get_job_runner().submit(_job, job_id=f"chat-{session_id}")
    return {"ok": True, "session_id": session_id, "job_id": job_id, "status": "in_progress"}


@router.post("/sessions/{session_id}/cancel")
def cancel_run(session_id: str, db: DbDep, user: Writer) -> dict[str, Any]:
    s = db.get(ChatSession, session_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")
    found = _chat_streams.cancel(session_id)
    return {"ok": True, "session_id": session_id, "stream_found": found}


@router.get("/sessions/{session_id}/stream")
async def chat_stream(session_id: str, db: DbDep, user: CurrentUser) -> StreamingResponse:
    s = db.get(ChatSession, session_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")

    async def _events() -> Any:
        handle = _chat_streams.get(session_id)
        if handle is None:
            yield _sse({"type": "run_idle", "session_id": session_id})
            return
        try:
            while True:
                try:
                    event = await asyncio.wait_for(handle.queue.get(), timeout=30.0)
                except TimeoutError:
                    yield _sse({"type": "ping"})
                    if handle.completed:
                        yield _sse({"type": "run_idle", "session_id": session_id})
                        return
                    continue
                yield _sse(event)
                if _chat_streams.is_terminal(event):
                    return
        finally:
            _chat_streams.remove(session_id)

    return StreamingResponse(_events(), media_type="text/event-stream")


# ─── HITL approval ────────────────────────────────────────────


@router.post("/actions/{action_id}/decide")
async def decide_action(
    action_id: int, body: _DecideBody, db: DbDep, user: Writer
) -> dict[str, Any]:
    """Approve or reject a pending Tier-2 action.

    Approval dispatches the registered handler — that's the single
    moment the destructive operation actually runs. Rejection just
    marks the row.

    After mark_decided + emit_resolved, we trigger a **follow-up agent
    turn** so the chat thread closes the loop with a user-visible
    assistant message ("已成功创建 1 个产品" / "已取消"). This mirrors
    OpenAI Assistants API's `submit_tool_outputs` → run-resume semantics:
    the dispatch result is fed back into the conversation and the model
    auto-generates the closing message. Without this, the chat just sits
    there after the user clicks approve — the agent's last message was
    "审批已创建，请确认", and there's no signal that anything happened
    afterwards. (Pattern adopted 2026-05-14 after a real user reported
    'agent doesn't tell me whether the action ran'.)

    `async def` because we need `asyncio.get_running_loop()` inside
    `_trigger_approval_followup` to wire the SSE handle's loop reference.
    """
    row = fetch_pending(db, action_id, user_id=user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "审批请求不存在或不属于你")
    if row.status != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"审批请求 {action_id} 已被处理 (status={row.status})",
        )

    if body.decision == "reject":
        result = {"notes": body.notes} if body.notes else None
        mark_decided(db, row, status="rejected", decided_by=user.id, result=result)
        emit_resolved(row.session_id, row.id, "rejected", result)
        await _trigger_approval_followup(
            session_id=row.session_id,
            user_id=user.id,
            user_role=user.role,
            action=row.action,
            action_id=row.id,
            decision="rejected",
            result=result or {},
        )
        return {"action_id": row.id, "status": "rejected"}

    # decision == "approve"
    spec = get_spec(row.action)
    if spec is None:
        # Action was registered when propose() ran but is now gone.
        # Treat as a server-side bug — fail loudly and don't dispatch.
        mark_decided(
            db,
            row,
            status="failed",
            decided_by=user.id,
            result={"error": f"action '{row.action}' is no longer registered"},
        )
        emit_resolved(
            row.session_id, row.id, "failed", {"error": "action not registered"}
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"action '{row.action}' is not registered on this server",
        )

    from agent.runtime.deps import V3Deps

    deps = V3Deps(db=db, user_id=user.id, user_role=user.role)
    from domains.identity import service as identity_service
    try:
        current = identity_service.get_business_user(db, user.id)
        if deps.auth_session_id:
            identity_service.assert_session_active(db, user.id, deps.auth_session_id)
        deps.user_role = current.role
        if spec.args_schema is not None:
            # Schema-typed dispatch. Re-validate at dispatch time too —
            # the row could be days old, schema may have evolved, and
            # "validate at both boundaries" is the defense-in-depth
            # pattern (Temporal Update Validator + handler both check).
            try:
                args_obj = spec.args_schema.model_validate(dict(row.payload or {}))
            except Exception as parse_exc:
                # If the persisted payload no longer matches the current
                # schema, surface that loudly — old rows shouldn't get
                # silently misinterpreted.
                raise ValueError(
                    f"persisted payload no longer satisfies "
                    f"{spec.args_schema.__name__}: {parse_exc}"
                ) from parse_exc
            result = spec.dispatch(deps, args_obj)
        else:
            result = spec.dispatch(deps, row.target_id, dict(row.payload or {}))
    except identity_service.AccountInactive as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except Exception as exc:
        logger.exception("decide_action %s dispatch failed", action_id)
        err = {"error": f"{type(exc).__name__}: {exc}"}
        mark_decided(db, row, status="failed", decided_by=user.id, result=err)
        emit_resolved(row.session_id, row.id, "failed", err)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, f"dispatch failed: {exc}"
        ) from exc

    mark_decided(db, row, status="approved", decided_by=user.id, result=result)
    emit_resolved(row.session_id, row.id, "approved", result)
    await _trigger_approval_followup(
        session_id=row.session_id,
        user_id=user.id,
        user_role=user.role,
        action=row.action,
        action_id=row.id,
        decision="approved",
        result=result or {},
    )
    return {"action_id": row.id, "status": "approved", "result": result}


# ─── Approval follow-up (Interrupt+Resume closure) ────────────


# Synthetic-input sentinel — the frontend's MessageView hides any user
# message that starts with this so the dispatch trigger doesn't show as
# a fake user bubble in the chat. Keep stable across releases; changing
# it requires a coordinated frontend deploy.
_APPROVAL_FOLLOWUP_MARKER = "[__APPROVAL_FOLLOWUP__]"


async def _trigger_approval_followup(
    *,
    session_id: str,
    user_id: int,
    user_role: str,
    action: str,
    action_id: int,
    decision: str,
    result: dict[str, Any],
) -> None:
    """Spin a synthetic agent turn that closes the loop after the user
    clicks approve / reject on a Tier-2 action card.

    Why: the agent's last assistant message was something like "审批已
    创建，请确认"; without a follow-up turn, the chat thread sits frozen
    after the user clicks. This re-opens the agent with the dispatch
    result baked into a user-shaped message — the model produces a
    1-2 sentence acknowledgement (`"✅ 已成功创建 1 个产品"`).

    Implementation notes:
    - Synthetic input is delivered as a user message (general-agent's
      `add_user` is the only entry point), prefixed with
      `_APPROVAL_FOLLOWUP_MARKER` so the frontend filters it out of the
      visible chat. Industry reference: OpenAI Assistants `submit_tool_outputs`
      injects the same way — as conversational input the model can read.
    - A fresh SSE handle is registered to receive the new run's events;
      the frontend must re-subscribe after a successful /decide call
      (handled in v3-chat-store handleDecide).
    - The job runs fire-and-forget — /decide returns 200 immediately so
      the UI's "请稍候" spinner doesn't hang on the follow-up's wall
      clock (typically 1-3s).
    """
    handle = _chat_streams.register(session_id)
    handle.attach_loop(asyncio.get_running_loop())

    # Soft defaults — let action-specific skills (e.g. master-data-upload
    # Step 5b) override with structured reflection templates when the
    # result is nontrivial (errors > 0, partial success, etc.). Simple
    # actions (delete_order with `{ok: true}`) naturally fit a one-liner;
    # complex actions (commit_upload_batch with errors + error_details)
    # warrant a structured summary. Don't hardcode either constraint here.
    #
    # LONG-TERM (Phase 2): migrate per-action response shape into
    # `ActionSpec.followup_template` (see rules/instruct_004_action_reflection_pattern.md).
    if decision == "approved":
        synthetic = (
            f"{_APPROVAL_FOLLOWUP_MARKER}\n"
            f"审批 #{action_id} ({action}) 已通过。\n"
            f"执行结果: {json.dumps(result, ensure_ascii=False)}\n"
            f"请向用户报告结果。详略由内容决定：成功且 errors=0 一句话即可；"
            f"如 result 中 errors > 0 或含 error_details，请按对应 skill 的反思规范输出（"
            f"如 master-data-upload 的 Step 5b 模板）。"
            f"不要原样贴 JSON。如需更深细节，可调只读工具（如 preview_upload）。"
            f"不要发起新的 propose_action。"
        )
    else:
        # rejected / failed
        synthetic = (
            f"{_APPROVAL_FOLLOWUP_MARKER}\n"
            f"审批 #{action_id} ({action}) 被{decision}。\n"
            f"细节: {json.dumps(result, ensure_ascii=False)}\n"
            f"请向用户简要说明操作未执行。如失败带有诊断信息，"
            f"按对应 skill 的规范汇报。不要重新发起 propose_action。"
        )

    async def _job() -> None:
        try:
            handle.emit({"type": "run_started", "session_id": session_id})
            await asyncio.to_thread(
                _run_agent_blocking, session_id, user_id, user_role, synthetic
            )
            handle.emit({"type": "run_completed", "session_id": session_id})
        except Exception as exc:
            logger.exception("approval followup failed for session %s", session_id)
            handle.emit(
                {"type": "run_error", "session_id": session_id, "error": str(exc)}
            )
        finally:
            handle.completed = True

    get_job_runner().submit(_job, job_id=f"chat-followup-{action_id}")


# ─── Internal helpers ─────────────────────────────────────────


def _run_agent_blocking(
    session_id: str,
    user_id: int,
    user_role: str,
    text: str,
    model: str | None = None,
    page_context: str | None = None,
) -> None:
    """Run the agent synchronously in a worker thread.

    Streaming model (cf. ADR-0009):
    - The agent's LLM client uses `stream=True` and fires per-token
      callbacks via `StreamCallbacks`.
    - We register callbacks here that push SSE events into the
      session's `_chat_streams` handle, which the open SSE response
      reads from its asyncio.Queue.
    - Tool *results* don't come through the LLM stream; they arrive
      synchronously after `dispatch()`. We hook `on_step` for that.

    Tool-text suppression: `on_text_delta` already filters out the text
    that fires in tool-calling turns (general-agent's LLM layer does it
    based on hermes' rule), so the SSE consumer never sees ghost
    "Let me check…" chatter.
    """
    from general_agent import StreamCallbacks
    from infrastructure.db import session as session_module

    handle = _chat_streams.get(session_id)

    def _emit(event_type: str, **payload: Any) -> None:
        if handle is None:
            return
        handle.emit({"type": event_type, "session_id": session_id, **payload})

    callbacks = StreamCallbacks(
        on_text_delta=lambda t: _emit("text_delta", delta=t),
        on_reasoning_delta=lambda t: _emit("reasoning_delta", delta=t),
        on_tool_call_started=lambda call_id, name: _emit(
            "tool_call_started", call_id=call_id, tool_name=name
        ),
        on_tool_args_delta=lambda call_id, delta: _emit(
            "tool_args_delta", call_id=call_id, delta=delta
        ),
        on_assistant_message_done=lambda: _emit("assistant_message_done"),
        # Fired once at the start of a run when one or more SKILL.md
        # files match the user's task. Lets the UI render a "skill
        # activated" chip before the LLM starts streaming.
        on_skills_activated=lambda items: _emit("skill_activated", skills=items),
    )

    # Track whether the streaming path already pushed assistant text to
    # the UI. If it did, the agent's final-step text is just a redundant
    # echo (it's the same content general-agent returns from `agent.run`).
    # If it didn't (Kimi-style: model emits no text, calls the `finish`
    # tool to deliver the answer), we synthesize the assistant bubble
    # ourselves from the final step.
    text_streamed = {"v": False}
    original_on_text = callbacks.on_text_delta

    def _wrapped_on_text(delta: str) -> None:
        text_streamed["v"] = True
        if original_on_text is not None:
            original_on_text(delta)

    callbacks.on_text_delta = _wrapped_on_text
    final_answer: dict[str, str] = {"text": ""}

    def _step_hook(step: Any) -> None:
        kind = getattr(step, "kind", None)
        # Tool dispatch result — ship it to the UI right away so users
        # see the tool finish before the next assistant turn streams.
        if kind == "tool":
            _emit(
                "tool_result",
                tool_name=getattr(step, "name", ""),
                args=getattr(step, "args", None) or {},
                result=getattr(step, "result", "") or "",
            )
        elif kind == "final":
            # If general-agent never streamed any assistant text this run
            # (e.g. the model went straight to `finish(answer=…)` like
            # Kimi K2 does), the user sees only tool steps and no bubble.
            # Synthesize one from the final step so the answer is visible
            # both live (via SSE) and on reload (persisted below).
            result = getattr(step, "result", "") or ""
            if result and not text_streamed["v"]:
                _emit("text_delta", delta=result)
                _emit("assistant_message_done")
                final_answer["text"] = result

    db = session_module.SessionLocal()
    try:
        # If caller didn't pin a model (e.g. approval follow-up path that
        # doesn't have the session row in hand), pick up the session's
        # stored preference. NULL → factory falls back to settings default.
        if model is None:
            row = db.get(ChatSession, session_id)
            if row is not None:
                model = row.model
        agent = create_v3_chat_agent(
            db=db,
            user_id=user_id,
            user_role=user_role,
            session_id=session_id,
            stream_callbacks=callbacks,
            on_step=_step_hook,
            model=model,
            page_context=page_context,
        )
        agent.run(text)
        # Persist the synthesized assistant message so it survives a reload.
        # Without this the chat history would only have the `finish` tool
        # call + tool result, neither of which the message view renders as
        # an assistant bubble.
        if final_answer["text"]:
            _persist_synthetic_assistant(db, session_id, final_answer["text"], model)
        db.commit()
    finally:
        db.close()


def _persist_synthetic_assistant(
    db: Any, session_id: str, content: str, model: str | None
) -> None:
    """Append an assistant ChatMessage row with the agent's final answer.

    Used when the model didn't stream text and only emitted a terminator
    tool call (Kimi K2 pattern). The agent loop already persisted the
    tool_call + tool_result rows via SessionStore; we add a final
    assistant row carrying the answer as plain content so the chat thread
    renders a bubble.
    """
    last_seq = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.sequence.desc())
        .first()
    )
    next_seq = (last_seq.sequence + 1) if last_seq is not None else 0
    db.add(
        ChatMessage(
            session_id=session_id,
            sequence=next_seq,
            role="assistant",
            parts=[
                {
                    "type": "raw",
                    "schema": "openai-chat-completion",
                    "data": {"role": "assistant", "content": content},
                }
            ],
            model=model,
            finished_at=datetime.utcnow(),
        )
    )


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _uuid() -> str:
    import uuid

    return uuid.uuid4().hex


def _session_to_dict(s: ChatSession) -> dict[str, Any]:
    return {
        "id": s.id,
        "user_id": s.user_id,
        "title": s.title,
        "status": s.status,
        "model": s.model,
        "total_prompt_tokens": s.total_prompt_tokens,
        "total_completion_tokens": s.total_completion_tokens,
        "estimated_cost_usd": s.estimated_cost_usd,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def _message_to_dict(m: ChatMessage) -> dict[str, Any]:
    return {
        "id": m.id,
        "session_id": m.session_id,
        "sequence": m.sequence,
        "role": m.role,
        "parts": m.parts,
        "model": m.model,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }
