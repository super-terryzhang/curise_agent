"""Loop-control tools. Toolset: "control". Contains the `finish` terminator."""

from __future__ import annotations

from ..tools import tool


@tool(toolset="control", terminator=True, emoji="✅")
def finish(answer: str) -> str:
    """Mark the task complete and return the final answer. Call this EXACTLY ONCE when you have fully answered the user. Do not call other tools in the same turn.

    Args:
        answer: The complete, user-facing final answer. Markdown OK.
    """
    return answer
