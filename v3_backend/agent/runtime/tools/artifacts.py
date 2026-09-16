"""present_artifact — agent hand-off to a frontend UI component.

This tool is the **only** way the chat agent can ask the frontend to
render a structured UI. The pattern is the declarative half of
generative UI (Google A2UI / Vercel JSON-Render / CopilotKit AG-UI,
all 2025-2026):

  - Agent NEVER writes HTML bytes. It writes a small JSON envelope.
  - Component name must be in `artifacts.py`'s catalog. Unknown names
    return an error string the agent can recover from.
  - Data dict carries reference IDs only (e.g. `batch_id=12`). The
    frontend hydrates the actual table values from a REST endpoint.
  - Narration is a separate field — the chat-stream caption that
    accompanies the artifact in the conversation panel.

Why we built this:
    2026-05-19 incident — agent narrated `pack_size` values it hadn't
    actually read. Probe results (`scripts/probe_html_hallucination.py`):
    Gemini drift = 0% at ≤100 rows but 2.83% at 200 rows; multi-turn 50
    rows produces silent truncation (only 20/50 rows rendered, no warning).
    Pulling rendering out of the LLM context entirely closes both gaps.
"""

from __future__ import annotations

import json
from typing import Any

from general_agent import ToolContext, tool

from agent.runtime import artifacts
from agent.runtime.deps import get_deps
from apps.http import _chat_streams


def _format_catalog_for_docstring() -> str:
    """Build the live catalog list that gets glued onto the tool's
    docstring. This way the agent's prompt always lists the current set
    of components, no matter which order modules load in."""
    lines = []
    for spec in artifacts.known_components():
        required = ", ".join(
            f"{k}: {v.__name__ if isinstance(v, type) else v}"
            for k, v in spec.schema.items()
        )
        optional = ""
        if spec.optional_schema:
            opts = ", ".join(
                f"{k}?: {v.__name__ if isinstance(v, type) else v}"
                for k, v in spec.optional_schema.items()
            )
            optional = f", {opts}"
        body = (
            f"      - `{spec.name}` — {spec.description}\n"
            f"        data shape: {{{required}{optional}}}"
        )
        # Surface enums so the agent knows the legal values without
        # trial-and-error. Format: indented bullets after the data shape.
        if spec.enums:
            enum_lines = "\n".join(
                f"          {path} ∈ {list(values)}"
                for path, values in spec.enums.items()
            )
            body = body + "\n" + enum_lines
        lines.append(body)
    return "\n".join(lines) if lines else "      (no components registered)"


@tool(toolset="business", emoji="🖼️")
def present_artifact(
    component: str,
    narration: str,
    data: str = "{}",
    *,
    ctx: ToolContext,
) -> str:
    """Hand off a structured answer to the frontend's rich-UI panel.

    This is the **artifact output channel** — one of three lanes you
    have alongside plain `finish` (chat markdown) and `propose_action`
    (HITL approval). Reach for it when the answer is:

      - A **table / list** with ≥ 10 rows
      - A **diff** (row-by-row before / after)
      - A **comparison** between named entities
      - **Hour-by-hour / time-series** data (weather, prices, metrics)
      - Any structured grid the user will sort, filter, or scan

    Do NOT use it for:
      - Single-fact answers ("It's Tuesday")
      - 1-3 row outputs (a small markdown table reads better)
      - Conversational replies, follow-up questions, clarifications
      - Conversational acknowledgments ("好的，已提交")

    How it works: you pick a registered component name, pass a small
    `data` payload (either reference IDs for backend fetch, or the actual
    rows for `generic_table`), and write a 1-3 sentence `narration`.
    The frontend renders the panel; your `finish` text becomes a short
    chat caption alongside it.

    Available components:
{catalog}

    Data path varies by component:
      - **Domain components** (e.g. `upload_diff_viewer`): pass reference
        IDs only (`{{"batch_id": 12}}`); the frontend fetches the actual
        data from a REST endpoint. Zero hallucination guarantee.
      - **`generic_table`** (universal fallback): inline `columns` +
        `rows` directly. The panel is labelled "AI-generated content"
        because the data came from you. Cap: 100 rows.
      - **`narration_only`**: text-only fallback when no component fits
        the shape.

    Narration template (in user's language; Chinese by default):
      "{{N}} <kind>{{counts}}. 最值得注意的是 {{one concrete observation
       referencing a real row/value}}."

    Two to three sentences max. Generic phrases like "上传预览" or
    "请查看详情" are unacceptable — pick a row whose value is surprising
    (price ×5, currency change, outlier) and name it.

    Args:
        component: One of the registered names listed above. Inventing
            a name returns an error; the error suggests fallbacks
            (`generic_table` / `narration_only`).
        narration: 1-3 sentence user-language caption shown beside the
            panel. Empty is rejected.
        data: JSON object. Shape depends on the component (see above).
            Default empty `{{}}` is valid for `narration_only`.
    """
    deps = get_deps(ctx)

    # Parse `data`. Gemini's OpenAI-compat tool schema declares string
    # but Gemini sometimes still passes a dict in tool_call.arguments
    # (the parameter name happens to be `data: str` per our signature,
    # but the LLM doesn't always honor that — e2e 2026-05-19 reproduced
    # this with `data={"batch_id": 1}` as a dict). Accept both shapes;
    # the catalog validation downstream is the same either way.
    if isinstance(data, dict):
        data_obj = data
    elif isinstance(data, str):
        try:
            data_obj = json.loads(data) if data else {}
        except json.JSONDecodeError as exc:
            return f"Error: `data` must be valid JSON — {exc}"
    elif data is None:
        data_obj = {}
    else:
        return f"Error: `data` must be a JSON string or object, got {type(data).__name__}"

    err = artifacts.validate(component, data_obj)
    if err is not None:
        return f"Error: {err}"

    if not narration or not narration.strip():
        return "Error: `narration` must be a non-empty one-line caption."

    session_id = getattr(getattr(ctx, "agent", None), "session_id", None)
    if not session_id:
        # The tool can still report success to the agent even without
        # an attached stream — the frontend can pick up the artifact
        # from the persisted message log on next render. But for the
        # SSE flow we need a session.
        session_id = ""

    # Best-effort SSE push so the frontend renders the artifact
    # immediately. If the stream has been GC'd (e.g. background job
    # outliving its HTTP request) the agent message itself still
    # carries the structured payload — the next page load can pick it
    # up from the conversation log.
    handle = _chat_streams.get(session_id) if session_id else None
    if handle is not None:
        handle.emit(
            {
                "type": "artifact",
                "session_id": session_id,
                "component": component,
                "data": data_obj,
                "narration": narration.strip(),
                "user_id": deps.user_id,
            }
        )

    # The string we hand back to the agent is intentionally tiny — it
    # acknowledges dispatch without echoing `data` (which the agent
    # already wrote). Echoing back would burn tokens AND tempt the
    # agent to "re-summarise" what it just sent. The probe data
    # showed agents stop hallucinating when they're not invited to
    # re-state the structured payload.
    return (
        f"Artifact dispatched: component='{component}', "
        f"data_keys={sorted(data_obj.keys())}, "
        f"narration='{narration.strip()[:80]}'"
    )


# Dynamically rewrite the docstring with the live catalog at import time.
# This way `enabled_v3_business_tool_names()` callers, the LLM tool
# schema, and any /tools/list endpoint all see the same up-to-date list.
present_artifact.__doc__ = present_artifact.__doc__.format(  # type: ignore[union-attr]
    catalog=_format_catalog_for_docstring()
)
