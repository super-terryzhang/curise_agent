"""Shell + calc tools. Toolset: "shell"."""

from __future__ import annotations

import re
import subprocess

from ..tools import ToolContext, tool


def _bash_available() -> bool:
    """Disable bash tool when Agent was constructed with allow_bash=False."""
    # Evaluated at view-definition time; the actual enforcement is per-Agent
    # via ctx.allow_bash. `check_fn` here is just a soft signal.
    return True


@tool(toolset="shell", emoji="🖥️")
def bash(command: str, timeout: int = 30, *, ctx: ToolContext) -> str:
    """Run a shell command in the workspace. Use for quick checks, parsing, computation. Avoid destructive commands.

    Args:
        command: The shell command (runs via /bin/sh).
        timeout: Max seconds before kill (default 30).
    """
    if not ctx.allow_bash:
        return "[tool-error] bash is disabled in this agent"

    # Pre-exec safety gate: dangerous-pattern detection + user approval.
    # Mirrors hermes `check_all_command_guards` but trimmed to what fits here.
    if ctx.approval is not None:
        from ..approval import gate_command
        allowed, reason = gate_command(command, ctx.approval)
        if not allowed:
            return reason

    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout)),
            cwd=str(ctx.workspace),
        )
    except subprocess.TimeoutExpired:
        return f"[tool-error] timeout after {timeout}s"
    out = (proc.stdout or "") + (("\nSTDERR:\n" + proc.stderr) if proc.stderr else "")
    return f"exit={proc.returncode}\n{out}"[:12000]


@tool(toolset="shell", emoji="🧮")
def calc(expression: str) -> str:
    """Evaluate a pure arithmetic expression. Safe: no names, no function calls.

    Args:
        expression: e.g. "3*(4+5)/2".
    """
    if not re.fullmatch(r"[\d\s\+\-\*\/\.\(\)%]+", expression):
        return "[tool-error] only digits and + - * / % ( ) allowed"
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))  # noqa: S307
    except Exception as e:
        return f"[tool-error] {e}"
