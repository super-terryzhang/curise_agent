#!/usr/bin/env python3
"""Architecture enforcement — runs in CI and as a pre-commit hook.

Fails with non-zero exit code if any of the ADR-0006 boundary rules is
violated. Standard library only (no dependencies).

Rules enforced:
  1. domains/** and shared/** must not import `agent.*`
  2. agent/runtime/tools/** must not directly touch the DB
     (`db.commit`, `db.add`, `db.query`, `db.delete`, `db.flush`).
     Whitelist for DB writes elsewhere under agent/: agent/storage/**,
     agent/memory/**, agent/runtime/session_store.py,
     agent/runtime/memory_adapter.py.
  3. Cross-domain imports only via `service`, `schemas`, or the package
     entry (`from domains.<other> import <name>`). Importing
     `domains.<other>.models` or `.repository` from another domain is
     a violation.
  4. shared/** must not import from domains/agent/apps/infrastructure.
  5. apps/line/** must not import apps/http/** — the LINE entry-point
     and the web HTTP entry-point are siblings, not parent/child.
     Sharing belongs in domains, agent, or infrastructure.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


# ─── Rule matchers ─────────────────────────────────────────────

RULE_1_AGENT_IMPORT = re.compile(r"^\s*(?:from|import)\s+agent(?:\.|\s|$)", re.MULTILINE)
# Now also forbids `db.execute` — was the loophole that let agent tools
# write raw SQL bypassing domain services (root cause of the misnamed
# `total = len(items)` field that survived 4 tools). See 2026-05-11
# RCA: domain service had only stub list_X for non-products, so tools
# hand-rolled SQL → wrong counts.
RULE_2_DB_ACCESS = re.compile(r"\bdb\.(commit|add|query|delete|flush|execute)\b")
# Match `from domains.<X>.models` or `.repository` but allow service/schemas/__init__
RULE_3_CROSS_DOMAIN = re.compile(
    r"^\s*from\s+domains\.(\w+)\.(?!service|schemas|__init__)(\w+)",
    re.MULTILINE,
)
RULE_4_SHARED_LEAK = re.compile(
    r"^\s*(?:from|import)\s+(domains|agent|apps|infrastructure)(?:\.|\s|$)",
    re.MULTILINE,
)
RULE_5_LINE_HTTP_LEAK = re.compile(
    r"^\s*(?:from|import)\s+apps\.http(?:\.|\s|$)",
    re.MULTILINE,
)


@dataclass
class Violation:
    rule: str
    path: Path
    line_no: int
    line: str

    def format(self) -> str:
        rel = self.path.relative_to(REPO_ROOT)
        return f"  [{self.rule}] {rel}:{self.line_no}  →  {self.line.strip()}"


def _scan(path: Path, pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    hits: list[tuple[int, str]] = []
    for match in pattern.finditer(text):
        line_no = text.count("\n", 0, match.start()) + 1
        line = text.splitlines()[line_no - 1] if line_no - 1 < len(text.splitlines()) else ""
        hits.append((line_no, line))
    return hits


def _iter_py(relative_roots: list[str]) -> list[Path]:
    files: list[Path] = []
    for root in relative_roots:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        files.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return files


def check_rule_1() -> list[Violation]:
    """domains/** and shared/** must not import agent.*"""
    out: list[Violation] = []
    for path in _iter_py(["domains", "shared"]):
        for line_no, line in _scan(path, RULE_1_AGENT_IMPORT):
            out.append(Violation("RULE-1 business→agent", path, line_no, line))
    return out


# Tools with a legitimate reason to call `db.execute` directly:
#   - query_db.py: the WHOLE PURPOSE of this tool is to run raw SQL the
#                  agent constructs. Forbidding execute would defeat it.
#   - propose.py:  writes to v3_pending_actions which is agent-internal
#                  HITL state, not a business domain. Has no service.
_RULE_2_DB_WHITELIST: frozenset[str] = frozenset({
    "query_db.py",
    "propose.py",
})


def check_rule_2() -> list[Violation]:
    """agent/runtime/tools/** must not touch DB directly.

    Tools may only access the DB via V3Deps → domain services. Writes
    AND raw reads are both banned (the latter caught real bugs — see
    RCA on misnamed `total` field). Exempted tools whitelisted above.
    DB writes from agent/storage, agent/memory, and the runtime adapters
    (session_store, memory_adapter) are still fine — those aren't tools.
    """
    out: list[Violation] = []
    runtime_tools_dir = REPO_ROOT / "agent" / "runtime" / "tools"
    for path in _iter_py(["agent"]):
        try:
            path.relative_to(runtime_tools_dir)
        except ValueError:
            continue  # not under agent/runtime/tools/, skip
        if path.name in _RULE_2_DB_WHITELIST:
            continue
        for line_no, line in _scan(path, RULE_2_DB_ACCESS):
            out.append(Violation("RULE-2 agent→db-direct", path, line_no, line))
    return out


def check_rule_3() -> list[Violation]:
    """Cross-domain imports must go through service/schemas/__init__."""
    out: list[Violation] = []
    for path in _iter_py(["domains"]):
        rel = path.relative_to(REPO_ROOT / "domains")
        if not rel.parts:
            continue
        own_domain = rel.parts[0]
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in RULE_3_CROSS_DOMAIN.finditer(text):
            other_domain = match.group(1)
            submodule = match.group(2)
            if other_domain == own_domain:
                continue
            line_no = text.count("\n", 0, match.start()) + 1
            line = text.splitlines()[line_no - 1]
            out.append(
                Violation(
                    f"RULE-3 cross-domain internal: {own_domain}→{other_domain}.{submodule}",
                    path,
                    line_no,
                    line,
                )
            )
    return out


def check_rule_4() -> list[Violation]:
    """shared/** must not import business layers."""
    out: list[Violation] = []
    for path in _iter_py(["shared"]):
        for line_no, line in _scan(path, RULE_4_SHARED_LEAK):
            out.append(Violation("RULE-4 shared→business", path, line_no, line))
    return out


def check_rule_5() -> list[Violation]:
    """apps/line/** must not import apps/http/**."""
    out: list[Violation] = []
    line_dir = REPO_ROOT / "apps" / "line"
    for path in _iter_py(["apps"]):
        try:
            path.relative_to(line_dir)
        except ValueError:
            continue  # not under apps/line/
        for line_no, line in _scan(path, RULE_5_LINE_HTTP_LEAK):
            out.append(Violation("RULE-5 apps.line→apps.http", path, line_no, line))
    return out


# ─── Entry ─────────────────────────────────────────────────────


def main() -> int:
    violations: list[Violation] = []
    violations.extend(check_rule_1())
    violations.extend(check_rule_2())
    violations.extend(check_rule_3())
    violations.extend(check_rule_4())
    violations.extend(check_rule_5())

    if not violations:
        print("arch-check: OK (0 violations)")
        return 0

    print(f"arch-check: {len(violations)} violation(s)")
    for v in violations:
        print(v.format())
    print()
    print("See docs/adr/0006-module-boundary-enforcement.md for the rules.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
