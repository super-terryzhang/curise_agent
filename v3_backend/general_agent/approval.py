"""Approval / safety layer for bash.

Distilled from hermes-agent/tools/approval.py (916 LOC → ~180 LOC).

Pipeline:
    bash(command) -> detect_dangerous(command)
                  -> is_approved_this_session?  → skip prompt
                  -> approval_callback(cmd, reason, mode) -> decision
                  -> {allow_once, allow_session, deny}

Kept from hermes:
  - Regex pattern list with descriptive keys (compressed to the ~20 highest-ROI
    patterns — rm -rf, curl|sh, self-kill, system-file writes, fork bomb,
    git destructive, SQL DROP, kill -9 -1)
  - Normalize before match (strip ANSI, null bytes, NFKC for fullwidth)
  - Three modes:  "manual" (prompt) / "off" (YOLO, auto-approve) / "deny_all"
  - Session allowlist keyed by human-readable pattern description
  - Pluggable `approval_callback`; default = CLI blocking prompt with timeout

Skipped from hermes (out of scope for this library):
  - Smart-LLM approvals, gateway async approvals, permanent config-yaml
    allowlist, tirith security scan, per-container auto-approve.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Literal


Decision = Literal["allow_once", "allow_session", "deny"]
Mode = Literal["manual", "off", "deny_all"]


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------

# (regex, description) — description doubles as the session-allowlist key.
DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\s+(-[^\s]*\s+)*/", "delete in root path"),
    (r"\brm\s+-[^\s]*r", "recursive delete"),
    (r"\brm\s+--recursive\b", "recursive delete (long flag)"),
    (r"\bchmod\s+(-[^\s]*\s+)*(777|666|o\+[rwx]*w|a\+[rwx]*w)\b",
     "world-writable permissions"),
    (r"\bchown\s+(-[^\s]*)?R\s+root", "recursive chown to root"),
    (r"\bmkfs\b", "format filesystem"),
    (r"\bdd\s+.*if=", "disk copy"),
    (r">\s*/dev/sd", "write to block device"),
    (r"\bDROP\s+(TABLE|DATABASE)\b", "SQL DROP"),
    (r"\bDELETE\s+FROM\b(?!.*\bWHERE\b)", "SQL DELETE without WHERE"),
    (r"\bTRUNCATE\s+(TABLE)?\s*\w", "SQL TRUNCATE"),
    (r">\s*/etc/", "overwrite system config"),
    (r"\bsystemctl\s+(stop|disable|mask)\b", "stop/disable system service"),
    (r"\bkill\s+-9\s+-1\b", "kill all processes"),
    (r"\bpkill\s+-9\b", "force kill processes"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork bomb"),
    (r"\b(curl|wget)\b.*\|\s*(ba)?sh\b", "pipe remote content to shell"),
    (r"\b(bash|sh|zsh|ksh)\s+<\s*<?\s*\(\s*(curl|wget)\b",
     "execute remote script via process substitution"),
    (r"\b(cp|mv|install)\b.*\s/etc/", "copy/move file into /etc/"),
    (r"\bsed\s+-[^\s]*i.*\s/etc/", "in-place edit of system config"),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard"),
    (r"\bgit\s+push\b.*(-f\b|--force\b)", "git force push"),
    (r"\bgit\s+clean\s+-[^\s]*f", "git clean with force"),
    (r"\bgit\s+branch\s+-D\b", "git branch force delete"),
    (r"\bfind\b.*-delete\b", "find -delete"),
    (r"\bfind\b.*-exec\s+(/\S*/)?rm\b", "find -exec rm"),
    (r"\bxargs\s+.*\brm\b", "xargs with rm"),
]

_COMPILED = [(re.compile(pat, re.IGNORECASE), desc) for pat, desc in DANGEROUS_PATTERNS]

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def _normalize(command: str) -> str:
    """Strip common obfuscations before pattern matching."""
    s = _ANSI_RE.sub("", command)
    s = s.replace("\x00", "")
    s = unicodedata.normalize("NFKC", s)
    return s


def detect_dangerous(command: str) -> tuple[bool, str, str]:
    """Return (is_dangerous, pattern_key, description).

    pattern_key == description for simplicity (hermes uses both but then
    aliases them; we don't need the back-compat layer).
    """
    s = _normalize(command)
    for compiled, desc in _COMPILED:
        if compiled.search(s):
            return True, desc, desc
    return False, "", ""


# ---------------------------------------------------------------------------
# Approval state
# ---------------------------------------------------------------------------

@dataclass
class ApprovalState:
    mode: Mode = "manual"
    session_allowlist: set[str] = field(default_factory=set)
    # Callback signature: (command, description, mode) -> Decision.
    # If None, uses the default CLI prompt.
    callback: Callable[[str, str, Mode], Decision] | None = None
    timeout_seconds: int = 60

    def allow_for_session(self, pattern_key: str) -> None:
        self.session_allowlist.add(pattern_key)

    def is_allowed(self, pattern_key: str) -> bool:
        return pattern_key in self.session_allowlist


def default_cli_prompt(command: str, description: str, mode: Mode) -> Decision:
    """Blocking CLI prompt with timeout. Returns `deny` if no TTY or timeout."""
    if not sys.stdin.isatty():
        # Non-interactive: safest default is deny.
        return "deny"
    print()
    print(f"  ⚠️  DANGEROUS COMMAND: {description}")
    print(f"      {command}")
    print()
    print("      [o]nce  |  [s]ession  |  [d]eny")

    result: dict[str, str] = {"choice": ""}

    def _read() -> None:
        try:
            result["choice"] = input("      Choice [o/s/D]: ").strip().lower()
        except (EOFError, OSError):
            result["choice"] = ""

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout=60)
    if t.is_alive():
        print("\n      ⏱  Timeout — denying")
        return "deny"
    c = result["choice"]
    if c in ("o", "once"):
        return "allow_once"
    if c in ("s", "session"):
        return "allow_session"
    return "deny"


def gate_command(
    command: str,
    state: ApprovalState,
) -> tuple[bool, str]:
    """Run the pre-exec safety gate.

    Returns (allowed, reason_if_blocked). When allowed is False, the caller
    should refuse to execute and feed `reason_if_blocked` back to the model
    as the tool result.
    """
    # `off` mode = YOLO — everything allowed.
    if state.mode == "off":
        return True, ""
    # `deny_all` mode — refuse every bash. Useful for read-only agents.
    if state.mode == "deny_all":
        return False, "[blocked] bash is disabled (approval mode=deny_all)"

    is_dangerous, pattern_key, description = detect_dangerous(command)
    if not is_dangerous:
        return True, ""

    if state.is_allowed(pattern_key):
        return True, ""

    cb = state.callback or default_cli_prompt
    try:
        decision = cb(command, description, state.mode)
    except Exception as e:
        # Never let a buggy callback silently pass dangerous commands.
        return False, f"[blocked] approval callback raised: {e}"

    if decision == "allow_session":
        state.allow_for_session(pattern_key)
        return True, ""
    if decision == "allow_once":
        return True, ""
    return False, (
        f"[blocked] user denied dangerous command: {description}. "
        "Do NOT retry with the same approach — change strategy."
    )
