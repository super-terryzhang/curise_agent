"""Persistent-memory tools — remember/recall a markdown file. Toolset: "notes"."""

from __future__ import annotations

from ..tools import ToolContext, tool


@tool(toolset="notes", emoji="📝")
def remember(note: str, tag: str = "", *, ctx: ToolContext) -> str:
    """Append a note to persistent memory (markdown file). Use to save lessons, key facts, or summaries you want available next run.

    Args:
        note: Text to remember.
        tag: Short tag/category (optional).
    """
    if ctx.memory is None:
        return "[tool-error] memory not configured on this agent"
    ctx.memory.append(note, tag=tag or None)
    return "memory updated"


@tool(toolset="notes", emoji="🔎")
def recall(query: str = "", *, ctx: ToolContext) -> str:
    """Search persistent memory. Returns matching blocks, or tail of memory if query is empty.

    Args:
        query: Substring to search for.
    """
    if ctx.memory is None:
        return "[tool-error] memory not configured on this agent"
    return ctx.memory.search(query)
