"""SKILL.md loader — lightweight procedural knowledge.

A skill is a directory containing `SKILL.md` with YAML frontmatter and a
markdown body. When a skill's trigger keywords match the task, its body is
appended to the system prompt.

Example SKILL.md:
    ---
    name: research-report
    description: Produce a structured research report with citations.
    triggers: ["research", "report", "compare"]
    ---
    Follow this structure:
    1. Scope the question
    2. Gather 3+ sources via web_search
    3. Verify with web_fetch
    4. Synthesize with citations
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:  # soft fallback: hand-parse trivial frontmatter
    yaml = None


@dataclass
class Skill:
    name: str
    description: str
    triggers: list[str]
    body: str
    path: Path


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    fm_raw, body = m.group(1), m.group(2)
    if yaml is not None:
        try:
            fm = yaml.safe_load(fm_raw) or {}
        except Exception:
            fm = {}
    else:
        fm = {}
        for line in fm_raw.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm, body


class SkillLoader:
    """Scans one or more directories for SKILL.md files.

    Pass a single path (str/Path) or a list of paths. Multiple roots let
    you ship built-in skills alongside user skills from a different
    directory — both are merged, with later roots overriding same-named
    skills from earlier ones.
    """

    def __init__(self, roots: str | Path | list[str | Path]) -> None:
        if isinstance(roots, (str, Path)):
            self.roots: list[Path] = [Path(roots)]
        else:
            self.roots = [Path(r) for r in roots]
        self.skills: list[Skill] = []
        self._scan()

    def _scan(self) -> None:
        by_name: dict[str, Skill] = {}
        for root in self.roots:
            if not root.exists():
                continue
            for skill_md in sorted(root.rglob("SKILL.md")):
                try:
                    text = skill_md.read_text(encoding="utf-8")
                except Exception:
                    continue
                fm, body = _parse_frontmatter(text)
                name = fm.get("name") or skill_md.parent.name
                desc = fm.get("description", "")
                triggers_raw = fm.get("triggers") or []
                if isinstance(triggers_raw, str):
                    triggers = [t.strip() for t in triggers_raw.split(",") if t.strip()]
                else:
                    triggers = [str(t) for t in triggers_raw]
                by_name[name] = Skill(
                    name=name,
                    description=desc,
                    triggers=[t.lower() for t in triggers],
                    body=body.strip(),
                    path=skill_md,
                )
        self.skills = list(by_name.values())

    def match(self, task: str) -> list[Skill]:
        """Legacy keyword-substring match.

        Retained for backwards compatibility with hosts that still rely on
        keyword-based auto-activation. v3's runtime has moved to the
        Anthropic-style "catalog + load_skill" pattern (cf.
        `agent.runtime.tools.skills_loader`); see `list_metadata` and
        `get` for the methods used by that flow.
        """
        t = task.lower()
        hits = [s for s in self.skills if any(trig in t for trig in s.triggers)]
        return hits

    def get(self, name: str) -> Skill | None:
        """Look up a skill by its `name` frontmatter field. None if missing."""
        for s in self.skills:
            if s.name == name:
                return s
        return None

    def list_metadata(self) -> list[dict[str, str]]:
        """Return `[{name, description}]` for every loaded skill.

        Designed for "progressive disclosure" prompt injection — the host
        injects this catalog into the system prompt so the LLM can pick
        which skill to call `load_skill(name)` on, without paying the
        full body's token cost up-front.
        """
        return [
            {"name": s.name, "description": s.description or ""} for s in self.skills
        ]
