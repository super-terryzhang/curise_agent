"""Tool registry + @tool decorator + ToolContext.

Design (mirrors hermes-agent/tools/registry.py):

  - Module-level singleton `REGISTRY`.  Tool modules are imported once at
    startup and call `@tool(...)` at import time to self-register.

  - Each registered tool carries METADATA beyond just its schema:
      toolset        — group name ("web", "files", ...) for batch enable/disable
      requires_env   — list of env vars the tool needs
      check_fn       — callable returning True/False if the tool is usable right now
      is_terminator  — calling this tool ends the agent loop (e.g. `finish`)
      emoji          — display symbol for trace output

  - Handlers that need per-Agent state (workspace, memory, ...) declare a
    keyword-only `ctx: ToolContext` argument.  At dispatch time the registry
    inspects the handler signature and passes `ctx` only to those handlers.
    This avoids factory-building the registry for every Agent instance — one
    global registry serves many Agents, each with its own ctx.

  - Schema is inferred from Python type hints + Google-style docstrings,
    using `typing.get_type_hints()` to resolve PEP-563 string annotations.
"""

from __future__ import annotations

import inspect
import json
import re
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, get_args, get_origin


# ---------------------------------------------------------------------------
# ToolContext — runtime state injected into ctx-aware handlers
# ---------------------------------------------------------------------------

@dataclass
class ToolContext:
    """Per-Agent runtime state made available to tool handlers that opt in.

    `agent` is the parent Agent — tools like `delegate_task` use it to spawn
    children.  Typed as Any to avoid a circular import with agent.core.
    """

    workspace: Path
    memory: Any = None           # agent.memory.Memory instance
    allow_bash: bool = True
    agent: Any = None            # agent.core.Agent — set by Agent at init
    approval: Any = None         # agent.approval.ApprovalState
    extras: dict[str, Any] = field(default_factory=dict)

    def safe_path(self, rel: str) -> Path:
        """Resolve a path under workspace, refusing to escape it."""
        p = (self.workspace / rel).resolve()
        if not str(p).startswith(str(self.workspace.resolve())):
            raise ValueError(f"path escapes workspace: {rel}")
        return p


# ---------------------------------------------------------------------------
# Type → JSON Schema mapping
# ---------------------------------------------------------------------------

_PY_TO_JSON: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _json_type(py_type: Any) -> dict[str, Any]:
    if py_type is inspect.Parameter.empty or py_type is Any:
        return {"type": "string"}
    origin = get_origin(py_type)
    if origin is typing.Union:
        args = [a for a in get_args(py_type) if a is not type(None)]
        if len(args) == 1:
            return _json_type(args[0])
        return {"type": "string"}
    if origin in (list, typing.List):
        args = get_args(py_type)
        item = _json_type(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item}
    if origin in (dict, typing.Dict):
        return {"type": "object"}
    if py_type in _PY_TO_JSON:
        return {"type": _PY_TO_JSON[py_type]}
    return {"type": "string"}


def _parse_docstring(doc: str | None) -> tuple[str, dict[str, str]]:
    if not doc:
        return "", {}
    lines = inspect.cleandoc(doc).splitlines()
    desc_lines: list[str] = []
    arg_descs: dict[str, str] = {}
    in_args = False
    current: str | None = None
    for raw in lines:
        line = raw.rstrip()
        if re.match(r"^\s*Args?\s*:\s*$", line):
            in_args = True
            continue
        if in_args and re.match(r"^\s*(Returns?|Raises?|Yields?|Examples?)\s*:\s*$", line):
            in_args = False
            continue
        if in_args:
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]*\))?\s*:\s*(.*)$", line)
            if m:
                current = m.group(1)
                arg_descs[current] = m.group(2).strip()
            elif current and line.strip():
                arg_descs[current] += " " + line.strip()
        else:
            if line.strip():
                desc_lines.append(line.strip())
    return " ".join(desc_lines).strip(), arg_descs


# ---------------------------------------------------------------------------
# Tool + ToolRegistry
# ---------------------------------------------------------------------------

@dataclass
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    func: Callable[..., Any]
    toolset: str = "core"
    requires_env: list[str] = field(default_factory=list)
    check_fn: Callable[[], bool] | None = None
    is_terminator: bool = False
    emoji: str = "⚡"
    _wants_ctx: bool = False     # detected from signature

    def available(self) -> bool:
        """True if this tool can run right now (env + check_fn)."""
        import os
        for env in self.requires_env:
            if not os.environ.get(env):
                return False
        if self.check_fn is not None:
            try:
                return bool(self.check_fn())
            except Exception:
                return False
        return True

    def invoke(self, args: dict[str, Any], ctx: ToolContext | None = None) -> str:
        call_kwargs: dict[str, Any] = dict(args)
        if self._wants_ctx:
            call_kwargs["ctx"] = ctx
        try:
            result = self.func(**call_kwargs)
        except TypeError as e:
            return f"[tool-error] bad arguments: {e}"
        except Exception as e:
            return f"[tool-error] {type(e).__name__}: {e}"
        if isinstance(result, (dict, list)):
            try:
                return json.dumps(result, ensure_ascii=False, default=str)[:16000]
            except Exception:
                return str(result)[:16000]
        return str(result)[:16000]


class ToolRegistry:
    """Central tool catalogue. Module-level singleton `REGISTRY` is shared.

    Agents filter an enabled subset via `view(toolsets=[...])` — this returns
    a read-only view without mutating the global catalogue.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # ------- registration -------

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools and self._tools[tool.name].toolset != tool.toolset:
            # Different toolset claiming same name — warn by shadowing.
            pass
        self._tools[tool.name] = tool

    def deregister(self, name: str) -> None:
        self._tools.pop(name, None)

    # ------- lookup -------

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    # ------- toolset-aware queries -------

    def by_toolset(self, toolset: str) -> list[Tool]:
        return [t for t in self._tools.values() if t.toolset == toolset]

    def toolsets(self) -> list[str]:
        return sorted({t.toolset for t in self._tools.values()})

    def toolset_map(self) -> dict[str, list[str]]:
        """{toolset_name: [tool_names...]} — for introspection / docs."""
        out: dict[str, list[str]] = {}
        for t in self._tools.values():
            out.setdefault(t.toolset, []).append(t.name)
        for k in out:
            out[k].sort()
        return out

    # ------- views (for Agent to scope available tools) -------

    def view(self, tool_names: Iterable[str] | None = None) -> "ToolView":
        """Return a view limited to specific tool names (or all if None).

        The view exposes the subset of the registry the agent should see,
        while dispatch still goes through the single central registry.
        """
        names = set(tool_names) if tool_names is not None else set(self._tools.keys())
        return ToolView(self, names)

    # ------- dispatch -------

    def dispatch(
        self,
        name: str,
        args: dict[str, Any],
        ctx: ToolContext | None = None,
    ) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"[tool-error] unknown tool: {name}"
        return tool.invoke(args, ctx=ctx)


class ToolView:
    """Read-only slice of a ToolRegistry exposed to an Agent.

    The Agent only sees tools in the view; anything else in the global
    registry is hidden. Dispatch still delegates to the underlying registry.
    """

    def __init__(self, parent: ToolRegistry, names: set[str]) -> None:
        self._parent = parent
        self._names = set(names)

    def get(self, name: str) -> Tool | None:
        if name not in self._names:
            return None
        return self._parent.get(name)

    def names(self) -> list[str]:
        return sorted(n for n in self._names if n in self._parent._tools)

    def definitions(self, *, include_unavailable: bool = False) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for n in sorted(self._names):
            t = self._parent.get(n)
            if t is None:
                continue
            if not include_unavailable and not t.available():
                continue
            out.append(t.schema)
        return out

    def describe(self) -> dict[str, list[str]]:
        """{toolset: [tool_names]} limited to the view."""
        out: dict[str, list[str]] = {}
        for n in sorted(self._names):
            t = self._parent.get(n)
            if t is None:
                continue
            out.setdefault(t.toolset, []).append(n)
        return out

    def dispatch(self, name: str, args: dict[str, Any], ctx: ToolContext | None = None) -> str:
        if name not in self._names:
            return f"[tool-error] tool not enabled in this agent: {name}"
        return self._parent.dispatch(name, args, ctx=ctx)


# Module-level singleton. Tool modules import this and call `@tool(...)`.
REGISTRY = ToolRegistry()


# ---------------------------------------------------------------------------
# @tool decorator
# ---------------------------------------------------------------------------

def tool(
    _func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    toolset: str = "core",
    requires_env: list[str] | None = None,
    check_fn: Callable[[], bool] | None = None,
    terminator: bool = False,
    emoji: str = "⚡",
    registry: ToolRegistry | None = None,
) -> Callable[..., Any]:
    """Register a function as a tool.

    Usage:
        @tool(toolset="web", requires_env=["SERPER_API_KEY"], emoji="🔍")
        def web_search(query: str, *, ctx: ToolContext) -> str:
            '''Search Google via Serper.

            Args:
                query: The search query.
            '''
            ...
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        sig = inspect.signature(func)
        try:
            hints = typing.get_type_hints(func, include_extras=False)
        except Exception:
            hints = {}
        description, arg_docs = _parse_docstring(func.__doc__)
        props: dict[str, Any] = {}
        required: list[str] = []
        wants_ctx = False
        for pname, param in sig.parameters.items():
            if pname in ("self", "cls"):
                continue
            if pname == "ctx":
                # ctx is injected by the registry, never exposed to the model.
                wants_ctx = True
                continue
            annot = hints.get(pname, param.annotation)
            ptype = _json_type(annot)
            if pname in arg_docs:
                ptype["description"] = arg_docs[pname]
            props[pname] = ptype
            if param.default is inspect.Parameter.empty:
                required.append(pname)
        schema = {
            "type": "function",
            "function": {
                "name": name or func.__name__,
                "description": description or f"Tool {func.__name__}",
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        }
        t = Tool(
            name=name or func.__name__,
            description=description,
            schema=schema,
            func=func,
            toolset=toolset,
            requires_env=list(requires_env or []),
            check_fn=check_fn,
            is_terminator=terminator,
            emoji=emoji,
            _wants_ctx=wants_ctx,
        )
        (registry or REGISTRY).register(t)
        return func

    if _func is not None:
        return decorator(_func)
    return decorator


# ---------------------------------------------------------------------------
# Back-compat alias (old code used GLOBAL_REGISTRY)
# ---------------------------------------------------------------------------

GLOBAL_REGISTRY = REGISTRY
