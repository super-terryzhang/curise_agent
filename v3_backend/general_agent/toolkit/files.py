"""File tools — workspace-scoped read/write/append/list. Toolset: "files"."""

from __future__ import annotations

from ..tools import ToolContext, tool


@tool(toolset="files", emoji="📖")
def read_file(path: str, *, ctx: ToolContext) -> str:
    """Read a file from the workspace. Returns file content or an error string.

    Args:
        path: Path relative to workspace.
    """
    p = ctx.safe_path(path)
    if not p.exists():
        return f"[tool-error] file not found: {path}"
    try:
        return p.read_text(encoding="utf-8")[:16000]
    except Exception as e:
        return f"[tool-error] {e}"


@tool(toolset="files", emoji="✏️")
def write_file(path: str, content: str, *, ctx: ToolContext) -> str:
    """Write (overwrite) a file in the workspace. Use for final reports, intermediate notes, generated code.

    Args:
        path: Path relative to workspace.
        content: Full file contents.
    """
    p = ctx.safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} chars to {path}"


@tool(toolset="files", emoji="➕")
def append_file(path: str, content: str, *, ctx: ToolContext) -> str:
    """Append content to a file (creates if missing). Use to build long documents across multiple small calls instead of one huge write_file.

    Args:
        path: Path relative to workspace.
        content: Text to append.
    """
    p = ctx.safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(content)
    return f"appended {len(content)} chars to {path}"


@tool(toolset="files", emoji="📂")
def list_files(path: str = ".", *, ctx: ToolContext) -> str:
    """List files and subdirectories in the workspace.

    Args:
        path: Relative path to list (default workspace root).
    """
    p = ctx.safe_path(path)
    if not p.exists():
        return f"[tool-error] no such dir: {path}"
    items = []
    for entry in sorted(p.iterdir()):
        kind = "DIR " if entry.is_dir() else "FILE"
        try:
            size = entry.stat().st_size
        except Exception:
            size = 0
        items.append(f"{kind}  {size:>8}  {entry.name}")
    return "\n".join(items) if items else "(empty)"
