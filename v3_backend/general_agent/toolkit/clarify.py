"""`clarify` tool — let the agent ask the user a question mid-run.

Mirrors hermes-agent/tools/clarify_tool.py (141 LOC). The actual UI lives
at the platform layer (CLI prompt, gateway message, etc.) — this module
just defines the tool and delegates to a callback.

Contract:
    - `AgentConfig.clarify_callback: Callable[[question, choices], str]`
    - If no callback is configured, the tool returns a helpful error so
      the agent knows to pick a default itself.
    - Default implementation is a CLI blocking prompt (`default_cli_clarify`).

Supports two modes:
    - Multiple choice: pass `choices` (≤ 4 strings); user picks one or types "other".
    - Open-ended: omit `choices`; user types free-form.
"""

from __future__ import annotations

import json
import sys
import threading
from typing import Any, Callable

from ..tools import ToolContext, tool


MAX_CHOICES = 4
_CLARIFY_TIMEOUT_SEC = 120


ClarifyCallback = Callable[[str, list[str] | None], str]


def default_cli_clarify(question: str, choices: list[str] | None) -> str:
    """Blocking CLI prompt. Returns `""` on non-TTY or timeout."""
    if not sys.stdin.isatty():
        return ""
    print()
    print(f"  ❓  {question}")
    if choices:
        for i, c in enumerate(choices, 1):
            print(f"      {i}. {c}")
        print(f"      {len(choices) + 1}. Other (type your answer)")
    print()

    result: dict[str, str] = {"answer": ""}

    def _read() -> None:
        try:
            raw = input("      Your answer: ").strip()
        except (EOFError, OSError):
            return
        if choices and raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                result["answer"] = choices[idx]
                return
            if idx == len(choices):
                try:
                    result["answer"] = input("      (type your answer): ").strip()
                except (EOFError, OSError):
                    pass
                return
        result["answer"] = raw

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout=_CLARIFY_TIMEOUT_SEC)
    if t.is_alive():
        return ""
    return result["answer"]


@tool(toolset="clarify", emoji="❓")
def clarify(
    question: str,
    choices: list[str] | None = None,
    *,
    ctx: ToolContext,
) -> str:
    """Ask the user a question when you need clarification, a decision, or feedback BEFORE proceeding. Two modes:

      - Multiple choice: pass `choices` (up to 4 strings); the user picks one or types their own via the 5th "Other" option.
      - Open-ended: omit `choices`; the user types a free-form response.

    Use this when:
      - The task is ambiguous and you need the user to pick an approach
      - A decision has real trade-offs the user should weigh (not low-stakes)
      - You want post-task feedback on which of two artifacts they preferred

    Do NOT use this for:
      - Dangerous-command yes/no (the bash approval gate handles that)
      - Anything a sensible default would cover (just pick + state your assumption in finish)

    Args:
        question: The question text to present.
        choices: Up to 4 predefined answer strings. Omit for open-ended.
    """
    question = (question or "").strip()
    if not question:
        return "[tool-error] question is required"

    if choices is not None:
        if not isinstance(choices, list):
            return "[tool-error] choices must be a list of strings"
        choices = [str(c).strip() for c in choices if str(c).strip()]
        if len(choices) > MAX_CHOICES:
            choices = choices[:MAX_CHOICES]
        if not choices:
            choices = None

    cb: ClarifyCallback | None = None
    if ctx.extras:
        cb = ctx.extras.get("clarify_callback")
    if cb is None:
        cb = default_cli_clarify

    try:
        answer = cb(question, choices)
    except Exception as e:
        return f"[tool-error] clarify callback raised: {e}"

    answer = str(answer).strip()
    if not answer:
        return json.dumps({
            "question": question,
            "choices_offered": choices,
            "user_response": "",
            "note": "no response received — make a reasonable default and proceed",
        }, ensure_ascii=False)

    return json.dumps({
        "question": question,
        "choices_offered": choices,
        "user_response": answer,
    }, ensure_ascii=False)
