"""Subagent / delegation tool.  Toolset: "delegation".

Design (distilled from hermes-agent/tools/delegate_tool.py):

  - One tool: `delegate_task`.
  - Two modes, mutually exclusive:
        single — pass `goal` (+ optional `context`, `toolsets`)
        batch  — pass `tasks=[{goal, context, toolsets}, ...]` for parallel run
  - Context isolation: each child is a fresh Agent with its own conversation,
    trace, and step-loop.  Only the child's final answer returns to the parent.
  - Depth limit (`MAX_DEPTH`): children may not further delegate past N levels.
  - Concurrency limit (`MAX_CONCURRENT`): thread-pool bounded.
  - Toolset restriction: child toolsets must be a subset of parent's view,
    intersected with a hardcoded blocklist (no `delegate_task` recursion).
  - Each child inherits: LLM config, workspace path (NOT memory — children
    should not write to the parent's persistent notes).
"""

from __future__ import annotations

import concurrent.futures
import json
from typing import Any

from ..tools import ToolContext, tool


MAX_DEPTH = 2
MAX_CONCURRENT = 3
PER_CHILD_TIMEOUT_SEC = 600

# Tools children may NEVER have, regardless of what the parent had.
BLOCKED_FOR_SUBAGENTS: frozenset[str] = frozenset({
    "delegate_task",  # no recursive delegation via the same tool
    "remember",       # no writes to shared markdown memory
    "recall",         # symmetry: children should not read parent memory either
})


def _resolve_child_toolnames(parent_agent: Any, requested: list[str] | None) -> set[str]:
    """Resolve the child's allowed tool names.

    Rule: child's tools = (parent's visible tools)
                          ∩ (resolved from requested toolsets, or parent's set if None)
                          − BLOCKED_FOR_SUBAGENTS.
    """
    from .toolsets import resolve_many

    parent_names = set(parent_agent.view.names())
    if requested:
        requested_names = resolve_many(requested)
    else:
        # Inherit parent's visible set.
        requested_names = parent_names
    allowed = (parent_names & requested_names) - BLOCKED_FOR_SUBAGENTS
    return allowed


def _run_child(
    parent_agent: Any,
    task_index: int,
    goal: str,
    context: str | None,
    toolsets: list[str] | None,
    max_steps: int,
) -> dict[str, Any]:
    """Build and run a single child agent. Returns a dict the parent sees."""
    # Import here to avoid circular import at module load time.
    from ..core import Agent, AgentConfig
    from ..tools import REGISTRY

    allowed_names = _resolve_child_toolnames(parent_agent, toolsets)
    if not allowed_names:
        return {
            "task": task_index,
            "error": (
                "child agent has no tools available after applying blocklist "
                "(did you request a toolset the parent doesn't have?)"
            ),
        }

    # Carve a dedicated subdir under the parent workspace so parallel children
    # don't clobber each other's files. Parent can still read e.g.
    # `read_file("sub_0/report.md")` to aggregate results.
    parent_ws = parent_agent.workspace
    child_ws = parent_ws.child(f"sub_{task_index}")

    # Focused child system prompt.
    child_system = (
        "You are a focused subagent working on ONE delegated task. "
        "You have no memory of the parent's conversation — everything you need "
        "must be in the task + context below.\n\n"
        f"YOUR TASK:\n{goal}\n"
    )
    if context and context.strip():
        child_system += f"\nCONTEXT:\n{context}\n"
    child_system += (
        f"\nYour private workspace directory is `{child_ws.root}`. "
        "All files you create with write_file/append_file go there — the parent "
        "can read them back as `sub_{i}/<path>` once you finish.\n"
        "\nUse the available tools. When finished, call `finish` with a CONCISE "
        "summary (~5-15 lines) of: what you did, what you found, any files you "
        "created, any issues.  Your `finish` answer is the ONLY thing the parent "
        "agent sees — everything else is discarded."
    )

    parent_cfg = parent_agent.config
    child_cfg = AgentConfig(
        llm=parent_cfg.llm,
        max_steps=max_steps,
        workspace=str(child_ws.root),      # recorded for manifest; actual ws below
        skills_root=None,
        system_prompt=child_system,
        allow_bash=parent_cfg.allow_bash,
        verbose=False,
        toolsets=[],
        # Inherit the parent's approval policy so children don't silently
        # execute dangerous commands under a more permissive default.
        approval_mode=parent_cfg.approval_mode,
        approval_callback=parent_cfg.approval_callback,
    )
    child_view = REGISTRY.view(allowed_names)

    # Pass the carved Workspace object directly so the child doesn't rebuild
    # (and re-manifest) the same path.
    child = Agent(
        config=child_cfg,
        view=child_view,
        on_step=lambda step: None,
        workspace=child_ws,
    )
    child.depth = parent_agent.depth + 1

    try:
        summary = child.run(goal)
    except Exception as e:
        return {"task": task_index, "error": f"{type(e).__name__}: {e}"}

    tool_names = [s.name for s in child.trace if s.kind == "tool"]
    return {
        "task": task_index,
        "workspace": str(child_ws.root.relative_to(parent_ws.root)),
        "summary": summary,
        "steps": len(child.trace),
        "tools_used": tool_names,
    }


@tool(toolset="delegation", emoji="🔀")
def delegate_task(
    goal: str = "",
    context: str = "",
    toolsets: list[str] | None = None,
    tasks: list[dict[str, Any]] | None = None,
    max_steps: int = 15,
    *,
    ctx: ToolContext,
) -> str:
    """Spawn 1-3 subagents to work on isolated tasks in parallel. Each subagent has its own fresh conversation and can only use a subset of your tools. Only the subagent's final summary is returned to you — intermediate tool calls never enter your context.

    WHEN TO USE:
      - A sub-problem that would flood your context with intermediate data (e.g. scrape 5 URLs and extract one number)
      - Independent parallel research streams (e.g. compare framework A, B, C simultaneously)
      - Isolated reasoning (debug a file in depth without polluting the main plan)

    TWO MODES (pass ONE of `goal` or `tasks`):

      Single:  goal="...", context="...", toolsets=["web"]
      Batch:   tasks=[{"goal": "...", "toolsets": ["web"]}, {...}, {...}]  (up to 3)

    Args:
        goal: Single-task mode — what the subagent should accomplish. Be self-contained; the subagent knows nothing about your conversation.
        context: Background info the subagent needs: file paths, constraints, error messages. The more specific, the better.
        toolsets: Toolsets the subagent is allowed to use (default: inherit yours). Note: `delegate_task`, `remember`, `recall` are always blocked for subagents.
        tasks: Batch-mode list of `{goal, context?, toolsets?}` dicts (max 3). All run concurrently; results are aggregated.
        max_steps: Max tool-calling turns per subagent (default 15).
    """
    parent = ctx.agent
    if parent is None:
        return "[tool-error] delegate_task requires a parent agent context"

    depth = getattr(parent, "depth", 0)
    if depth >= MAX_DEPTH:
        return json.dumps({
            "error": (
                f"delegation depth limit reached ({MAX_DEPTH}). "
                "this subagent cannot spawn further subagents."
            )
        }, ensure_ascii=False)

    # Normalize into a task list.
    if tasks and isinstance(tasks, list) and len(tasks) > 0:
        if len(tasks) > MAX_CONCURRENT:
            return json.dumps({
                "error": (
                    f"too many tasks: {len(tasks)} given, max is {MAX_CONCURRENT}. "
                    "split into multiple delegate_task calls."
                )
            }, ensure_ascii=False)
        task_list = tasks
    elif goal and goal.strip():
        task_list = [{"goal": goal, "context": context, "toolsets": toolsets}]
    else:
        return "[tool-error] provide either 'goal' (single) or 'tasks' (batch)"

    # Validate each task has a goal.
    for i, t in enumerate(task_list):
        if not isinstance(t, dict) or not str(t.get("goal", "")).strip():
            return f"[tool-error] task {i} is missing a 'goal'"

    # Run.
    if len(task_list) == 1:
        t = task_list[0]
        result = _run_child(
            parent, 0,
            goal=t["goal"],
            context=t.get("context") or "",
            toolsets=t.get("toolsets"),
            max_steps=max_steps,
        )
        return json.dumps({"results": [result]}, ensure_ascii=False)[:16000]

    results: list[dict[str, Any]] = [None] * len(task_list)  # type: ignore[list-item]
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(MAX_CONCURRENT, len(task_list))
    ) as ex:
        future_to_idx = {
            ex.submit(
                _run_child,
                parent, i,
                t["goal"],
                t.get("context") or "",
                t.get("toolsets"),
                max_steps,
            ): i
            for i, t in enumerate(task_list)
        }
        for fut in concurrent.futures.as_completed(future_to_idx):
            idx = future_to_idx[fut]
            try:
                results[idx] = fut.result(timeout=PER_CHILD_TIMEOUT_SEC)
            except Exception as e:
                results[idx] = {"task": idx, "error": f"{type(e).__name__}: {e}"}
    return json.dumps({"results": results}, ensure_ascii=False)[:16000]
