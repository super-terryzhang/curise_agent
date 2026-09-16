"""Named toolset presets — map a keyword to a set of tools.

Toolsets compose via `includes`, matching hermes-agent's design. Resolution is
recursive and deduplicated.

    resolve_toolset("research")
      = tools of "research"  ∪  tools of every toolset it `includes`

Agents instantiate with `toolsets=["research"]` or `toolsets=["all"]`.
"""

from __future__ import annotations

from ..tools import REGISTRY


# Semantic presets. "tools" lists explicit tool NAMES (not functions), so we
# remain decoupled from the implementations themselves — a tool rename in a
# module doesn't break here, it just drops that tool from the preset.
TOOLSETS: dict[str, dict] = {
    # --- atomic groups mirroring the per-module toolsets ---
    "web": {
        "description": "Web search + fetch.",
        "tools": ["web_search", "web_fetch"],
        "includes": [],
    },
    "files": {
        "description": "Workspace-scoped file IO.",
        "tools": ["read_file", "write_file", "append_file", "list_files"],
        "includes": [],
    },
    "shell": {
        "description": "Bash + calculator.",
        "tools": ["bash", "calc"],
        "includes": [],
    },
    "notes": {
        "description": "Persistent markdown memory (remember/recall).",
        "tools": ["remember", "recall"],
        "includes": [],
    },
    "control": {
        "description": "Loop-control tools (finish).",
        "tools": ["finish"],
        "includes": [],
    },
    "delegation": {
        "description": "Spawn subagents to work on isolated tasks (delegate_task).",
        "tools": ["delegate_task"],
        "includes": [],
    },
    "planning": {
        "description": "In-session TODO list for planning/tracking multi-step work.",
        "tools": ["todo"],
        "includes": [],
    },
    "clarify": {
        "description": "Ask the user a question mid-run (multiple-choice or open-ended).",
        "tools": ["clarify"],
        "includes": [],
    },

    # --- semantic presets composed from atomic groups ---
    "core": {
        "description": "Minimum viable: files + control (no network, no shell).",
        "tools": [],
        "includes": ["files", "control"],
    },
    "research": {
        "description": "Web research + write findings to files + finish.",
        "tools": [],
        "includes": ["web", "files", "notes", "control"],
    },
    "coding": {
        "description": "Write code, run it, read output.",
        "tools": [],
        "includes": ["files", "shell", "control"],
    },
    "manager": {
        "description": "Coordinator agent: delegate subtasks + write final report.",
        "tools": [],
        "includes": ["delegation", "files", "control"],
    },
    "all": {
        "description": "Every registered tool.",
        "tools": [],
        "includes": ["web", "files", "shell", "notes", "control",
                     "delegation", "planning", "clarify"],
    },
}


def resolve_toolset(name: str, _seen: set[str] | None = None) -> set[str]:
    """Recursively expand a toolset name into a set of concrete tool names.

    Lookup order:
      1. TOOLSETS preset dict (composition via `includes`).
      2. Fall back to REGISTRY — any tool registered with `toolset=name` via
         `@tool(...)`.  This makes plugin-declared toolsets work without
         needing an entry in TOOLSETS.
    """
    _seen = _seen or set()
    if name in _seen:
        return set()
    _seen.add(name)

    spec = TOOLSETS.get(name)
    if spec is not None:
        out: set[str] = set(spec.get("tools", []))
        for inc in spec.get("includes", []):
            out.update(resolve_toolset(inc, _seen))
        return out

    # Fallback: treat `name` as a registered toolset tag.
    dynamic = {t.name for t in REGISTRY.by_toolset(name)}
    return dynamic


def resolve_many(names: list[str]) -> set[str]:
    """Expand several toolset names and union their tools.

    `names` may contain a mix of:
      - TOOLSETS preset keys ("research", "coding", ...)
      - dynamically-registered toolset names ("text" from a plugin)
      - literal tool names ("web_search")
    """
    out: set[str] = set()
    for n in names:
        if n in REGISTRY:
            out.add(n)
        else:
            out |= resolve_toolset(n)
    return out


def list_toolsets() -> list[dict]:
    """Introspection helper — used by `main.py --list-toolsets` and docs."""
    out = []
    for name, spec in sorted(TOOLSETS.items()):
        tools = sorted(resolve_toolset(name))
        out.append({
            "name": name,
            "description": spec.get("description", ""),
            "includes": spec.get("includes", []),
            "resolved_tools": tools,
            "tool_count": len(tools),
        })
    return out
