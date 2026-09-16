"""propose_action — write a Tier-2 operation to the approval queue.

This tool is the *only* way a chat agent can express "I want to delete
/ create / modify financial fields / commit a batch". It records intent;
execution happens later when the user clicks "approve" in the UI.

Design notes:

- The action name must be in `ACTION_DISPATCH`; unknown names are
  rejected before the row is even written. This keeps the LLM from
  inventing destructive operations the system never registered.
- The summary is what the user actually reads on the approval card.
  We make it a required parameter (rather than autogenerating) because
  the LLM can describe the operation in user-language better than we
  can stringify a payload.
- The tool emits an SSE event so the frontend can render the card
  without polling. If the stream isn't attached (e.g. background job
  outliving the HTTP request), the row is still in the DB — the next
  page load will pick it up.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError
from general_agent import ToolContext, tool

from agent.runtime import approvals
from agent.runtime.deps import get_deps
from apps.http import _chat_streams


@tool(toolset="business", emoji="🛂")
def propose_action(
    action: str,
    summary: str,
    target_kind: str = "",
    target_id: int = 0,
    payload: str = "{}",
    *,
    ctx: ToolContext,
) -> str:
    """Submit a destructive / financial / batch operation for user approval.

    Use this whenever you want to delete data, create new master-data
    rows, change a financial field (price, code, invoice/payment),
    commit a data-upload batch, or affect more than one row at a time.
    Never call domain CRUD directly for those cases.

    The user sees `summary` on the approval card — write it in their
    language ("Delete order #38 (PO 2025-001, Norwegian Sky)") not as
    JSON.

    Args:
        action: Registered action name. Must be one of the system's
            known actions; arbitrary names are rejected.
        summary: One-line description shown to the user on the approval
            card. Be specific — include the target's identifying fields.
        target_kind: Entity type the action affects (order / product /
            supplier / upload_batch / ...). Optional but recommended.
        target_id: Numeric id of the target row. 0 / omitted for
            create operations.
        payload: JSON object with the operation's arguments — fields to
            change, filter criteria, etc. Empty object is fine for
            simple deletes.
    """
    deps = get_deps(ctx)

    spec = approvals.get_spec(action)
    if spec is None:
        valid = ", ".join(approvals.known_action_names()) or "(none registered)"
        return (
            f"Error: action '{action}' is not registered. "
            f"Valid actions: {valid}."
        )

    if not summary or not summary.strip():
        return "Error: `summary` must describe the operation in user language."

    try:
        payload_obj = json.loads(payload) if payload else {}
        if not isinstance(payload_obj, dict):
            raise ValueError("payload must be a JSON object")
    except (ValueError, json.JSONDecodeError) as exc:
        return f"Error: invalid payload JSON — {exc}"

    session_id = getattr(getattr(ctx, "agent", None), "session_id", None)
    if not session_id:
        return "Error: agent has no active session_id (cannot persist approval row)."

    target_kind_resolved = (target_kind or spec.target_kind or "").strip()

    # Schema-typed spec: validate BEFORE writing a DB row, so an invalid
    # call surfaces as an Error string the agent self-corrects from (vs.
    # the legacy path where the failure shows up downstream as a
    # `status=failed` row polluting the approval queue — prod 2026-05-20
    # Action #8 was exactly this).
    #
    # Accept BOTH common LLM call styles:
    #   - Gemini-3 style: agent passes everything in `payload`
    #     (`payload='{"batch_id": 11}'`, target_id omitted)
    #   - Legacy style: agent passes the id as `target_id`, rest in payload
    #     (`target_id=11, payload='{}'`)
    # We merge them: payload wins on key conflicts (it's where the schema
    # naturally lives), but the projected id falls back to the explicit
    # `target_id` arg if payload didn't carry it. This matches Temporal's
    # "Update Validator" pattern: validators accept the same shape as
    # handlers and the rejection is structured.
    resolved_target_id: int | None
    if spec.args_schema is not None:
        candidate = dict(payload_obj)
        # If `target_id` was passed as a separate tool arg, splice it
        # into the candidate dict under the projected field name. We
        # don't know the field name a priori, so we use a heuristic:
        # for specs where target_id_from_args reads a specific attribute,
        # we try setting that attribute name. Failing that, we just let
        # validation surface what's missing — the error message is
        # already specific enough to drive a retry.
        if target_id and spec.target_id_from_args is not None:
            try:
                # Probe by validating with a dummy that has the id under
                # several common names. Cheapest robust path: try once
                # with payload as-is; on failure, try after splicing
                # target_id under any field the schema declares as an int.
                spec.args_schema.model_validate(candidate)
            except ValidationError:
                int_fields = [
                    name for name, info in spec.args_schema.model_fields.items()
                    if info.annotation is int and name not in candidate
                ]
                if len(int_fields) == 1:
                    candidate[int_fields[0]] = target_id
        try:
            args_obj = spec.args_schema.model_validate(candidate)
        except ValidationError as exc:
            return _format_validation_error(action, spec, exc)
        # Source of truth = validated args; mirror it into payload column
        # for forward-compat (legacy UI reads from payload).
        payload_obj = args_obj.model_dump()
        resolved_target_id = (
            spec.target_id_from_args(args_obj)
            if spec.target_id_from_args is not None
            else None
        )
    else:
        resolved_target_id = target_id or None

    row = approvals.create_pending(
        deps.db,
        session_id=session_id,
        user_id=deps.user_id,
        action=action,
        target_kind=target_kind_resolved,
        target_id=resolved_target_id,
        payload=payload_obj,
        summary=summary.strip(),
    )

    # Push to SSE so the frontend renders the approval card without
    # waiting for the next message turn. Best-effort — if the stream
    # has been GC'd the row is still in the DB.
    handle = _chat_streams.get(session_id)
    if handle is not None:
        handle.emit(
            {
                "type": "approval_request",
                "session_id": session_id,
                "action_id": row.id,
                "action": action,
                "target_kind": target_kind_resolved,
                "target_id": row.target_id,
                "payload": payload_obj,
                "summary": summary.strip(),
            }
        )

    return (
        f"Approval request #{row.id} created: {summary.strip()} "
        f"Waiting for the user to confirm before {action} runs."
    )


def _format_validation_error(
    action: str, spec: approvals.ActionSpec, exc: ValidationError
) -> str:
    """Build an Error: string that tells the agent exactly what was
    wrong + how to retry. Mirrors the data-driven self-correction style
    used by has_errors / generic_table validators — the goal is the
    NEXT turn fixes the call without a human in the loop.
    """
    issues: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        msg = err.get("msg", "invalid")
        issues.append(f"`{loc}`: {msg}" if loc else msg)

    # Show the schema fields so the agent has the canonical shape inline,
    # not just the validator's complaint. Empirically (prod 2026-05-19
    # artifact column-key bug) agents recover faster when the error
    # contains an example of the right shape, not just what was wrong.
    fields_desc = []
    assert spec.args_schema is not None
    for name, info in spec.args_schema.model_fields.items():
        ann = getattr(info.annotation, "__name__", str(info.annotation))
        required = "required" if info.is_required() else "optional"
        fields_desc.append(f"`{name}: {ann}` ({required})")
    fields_line = ", ".join(fields_desc)

    return (
        f"Error: action '{action}' args validation failed. "
        + " | ".join(issues)
        + f". Expected schema: {{{fields_line}}}. "
        f"Retry by calling `propose_action(action='{action}', "
        f"summary='...', payload='<JSON with the schema fields>')`. "
        f"The `payload` JSON is the canonical place for action args; "
        f"the legacy `target_id` parameter is optional and only used "
        f"when payload doesn't contain the id field."
    )


def emit_resolved(
    session_id: str, action_id: int, decision: str, result: dict[str, Any] | None
) -> None:
    """Helper for the HTTP /decide endpoint — pushes a resolution event
    on the same stream propose_action used. Tolerant of missing stream
    (the user may have closed the chat tab before approving)."""
    handle = _chat_streams.get(session_id)
    if handle is None:
        return
    handle.emit(
        {
            "type": "approval_resolved",
            "session_id": session_id,
            "action_id": action_id,
            "decision": decision,
            "result": result or {},
        }
    )
