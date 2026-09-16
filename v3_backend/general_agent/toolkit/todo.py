"""`todo` tool — in-session task list for the agent to plan and track work.

Mirrors hermes-agent/tools/todo_tool.py (268 LOC) with the same core design:
    - One tool, two modes: read (no args) / write (pass `todos` array)
    - Every call returns the full current list
    - Statuses: pending / in_progress / completed / cancelled
    - State lives on the Agent's ToolContext (`ctx.extras["todo_store"]`)
      so it's per-Agent, survives context compaction, NOT cross-session
    - `merge=True` updates existing items by id and appends new ones;
      `merge=False` (default) replaces the list entirely

Behavioral guidance is in the tool description, not in a system-prompt hook.
"""

from __future__ import annotations

import json
from typing import Any

from ..tools import ToolContext, tool


_VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}


class TodoStore:
    def __init__(self) -> None:
        self._items: list[dict[str, str]] = []

    @staticmethod
    def _validate(item: Any) -> dict[str, str]:
        if not isinstance(item, dict):
            raise ValueError(f"todo item must be a dict, got {type(item).__name__}")
        item_id = str(item.get("id", "")).strip()
        content = str(item.get("content", "")).strip()
        status = str(item.get("status", "pending")).strip().lower()
        if not item_id:
            raise ValueError("todo 'id' is required and cannot be empty")
        if not content:
            raise ValueError("todo 'content' is required and cannot be empty")
        if status not in _VALID_STATUSES:
            raise ValueError(
                f"invalid status {status!r}; must be one of {sorted(_VALID_STATUSES)}"
            )
        return {"id": item_id, "content": content, "status": status}

    def write(self, todos: list[dict[str, Any]], merge: bool = False) -> list[dict[str, str]]:
        if not merge:
            self._items = [self._validate(t) for t in todos]
            return self.read()

        existing = {item["id"]: item for item in self._items}
        for t in todos:
            item_id = str(t.get("id", "")).strip()
            if not item_id:
                continue
            if item_id in existing:
                if "content" in t and t["content"]:
                    existing[item_id]["content"] = str(t["content"]).strip()
                if "status" in t and t["status"]:
                    s = str(t["status"]).strip().lower()
                    if s in _VALID_STATUSES:
                        existing[item_id]["status"] = s
            else:
                validated = self._validate(t)
                existing[validated["id"]] = validated
                self._items.append(validated)
        # Re-sync ordered list with the updated `existing` map.
        self._items = [existing[i["id"]] for i in self._items if i["id"] in existing]
        return self.read()

    def read(self) -> list[dict[str, str]]:
        return [dict(i) for i in self._items]

    def format(self) -> str:
        if not self._items:
            return "(todo list is empty)"
        marks = {"pending": "[ ]", "in_progress": "[~]",
                 "completed": "[x]", "cancelled": "[-]"}
        lines = [
            f"{marks.get(i['status'], '[ ]')} {i['id']}: {i['content']}"
            for i in self._items
        ]
        return "\n".join(lines)


def _get_store(ctx: ToolContext) -> TodoStore:
    store = ctx.extras.get("todo_store") if ctx.extras else None
    if not isinstance(store, TodoStore):
        store = TodoStore()
        ctx.extras["todo_store"] = store
    return store


@tool(toolset="planning", emoji="📋")
def todo(
    todos: list[dict] | None = None,
    merge: bool = False,
    *,
    ctx: ToolContext,
) -> str:
    """Maintain an in-session TODO list for a multi-step task. Omit `todos` to READ the current list; provide a list to WRITE. Always returns the FULL current list after any write.

    Use this tool:
      - At the START of a task with ≥3 steps, to plan out the work
      - BEFORE each major step, to mark the previous one completed and the next `in_progress`
      - WHEN you discover new sub-tasks mid-flight (pass `merge=true` and the new items)

    Each todo item is `{id, content, status}`. Status is one of:
    `pending` / `in_progress` / `completed` / `cancelled`.

    Rules:
      - `id` is YOUR choice but must be stable across updates (e.g. "1", "research-a").
      - Only ONE item should be `in_progress` at any time.
      - Prefer small, concrete items ("fetch weather.com forecast") over vague ones ("research").
      - Keep the list short (< 15 items); refactor as you go.

    Args:
        todos: List of `{id, content, status}` dicts. Omit to read.
        merge: If false (default), replace the entire list. If true, update matching ids and append new ones.
    """
    store = _get_store(ctx)
    if todos is None:
        return json.dumps({"todos": store.read(), "formatted": store.format()},
                          ensure_ascii=False)
    try:
        current = store.write(todos, merge=merge)
    except ValueError as e:
        return f"[tool-error] {e}"
    return json.dumps({"todos": current, "formatted": store.format()},
                      ensure_ascii=False)
