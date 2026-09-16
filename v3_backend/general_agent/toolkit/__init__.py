"""Built-in tools package — fully pluggable.

**Auto-discovery**: every `.py` sibling in this directory (except `__init__`
and `toolsets`) is imported at package load, triggering `@tool` registration.
You do NOT need to touch this file when adding a new built-in module —
just drop a new file in.

**External plugins**: `load_external_tools([dirs...])` imports every `.py` in
each directory as an isolated plugin module, triggering the same @tool
registration.  This is how users add tools from outside the general-agent
source tree.
"""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import sys
import traceback
from pathlib import Path

from .toolsets import TOOLSETS, resolve_toolset, list_toolsets  # noqa: F401


_HERE = Path(__file__).parent


_EXCLUDE_FROM_AUTOIMPORT = {"toolsets"}


def _auto_import_builtins() -> list[str]:
    """Import every built-in tool module in this package. Return module names."""
    loaded: list[str] = []
    for m in pkgutil.iter_modules([str(_HERE)]):
        if m.name in _EXCLUDE_FROM_AUTOIMPORT or m.name.startswith("_"):
            continue
        importlib.import_module(f"{__name__}.{m.name}")
        loaded.append(m.name)
    return loaded


# Triggered on first import of agent.toolkit — all @tool decorators fire.
_LOADED_BUILTINS = _auto_import_builtins()


def load_external_tools(
    dirs: list[str | Path],
    *,
    quiet: bool = False,
) -> list[str]:
    """Import every `.py` under each dir as a plugin module.

    Each file is imported with a unique module name (`agent_plugins.<stem>`)
    so plugins can't collide with built-ins or with each other. Import
    failures are reported but never crash the agent — a bad plugin is
    skipped.

    Returns the list of successfully loaded plugin module names.
    """
    loaded: list[str] = []
    for d in dirs:
        dp = Path(d).expanduser().resolve()
        if not dp.exists() or not dp.is_dir():
            if not quiet:
                print(f"[plugin] skipping missing dir: {dp}")
            continue
        for py in sorted(dp.glob("*.py")):
            if py.name.startswith("_"):
                continue
            mod_name = f"agent_plugins.{dp.name}.{py.stem}"
            if mod_name in sys.modules:
                loaded.append(mod_name)
                continue
            spec = importlib.util.spec_from_file_location(mod_name, py)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            try:
                spec.loader.exec_module(mod)
                loaded.append(mod_name)
            except Exception as e:
                if not quiet:
                    print(f"[plugin-error] failed to load {py}: {e}")
                    traceback.print_exc()
                sys.modules.pop(mod_name, None)
    return loaded


__all__ = [
    "TOOLSETS",
    "resolve_toolset",
    "list_toolsets",
    "load_external_tools",
]
