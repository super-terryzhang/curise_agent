"""Workspace — the file-system sandbox each Agent operates in.

Distilled from hermes-agent/tools/environments/ + tools/terminal_tool.py but
scoped down for our single-backend (local) library use case.

Responsibilities:
  - Own a root directory; create it lazily.
  - Resolve paths safely (no escape outside root).
  - Carve out subdirectories for child agents so parallel subagents don't
    clobber each other's files.
  - Drop a manifest (`.agent_manifest.json`) with metadata on first use, for
    post-run forensics and log correlation.

Non-goals (vs hermes):
  - Multiple backends (docker/modal/etc).  general-agent is local-first.
  - task_id-keyed session pooling with idle cleanup.  We don't run long-lived
    sandboxes — each Agent.run() is ephemeral.
  - Binary upload/download wrappers.  Use `write_file` + base64 yourself if you
    need it; we'd only be wrapping stdlib.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


MANIFEST_NAME = ".agent_manifest.json"


class Workspace:
    """A directory-scoped sandbox with path-escape safety and subagent carving.

    Typical usage (inside Agent):

        ws = Workspace("./workspace", manifest_meta={"task": task})
        ctx.workspace = ws.root
        # ... run loop ...
        # For a child agent:
        child_ws = ws.child("sub_0")
        # child.workspace becomes child_ws.root
    """

    def __init__(
        self,
        root: str | Path,
        *,
        manifest_meta: dict[str, Any] | None = None,
        parent: "Workspace | None" = None,
        owned: bool = False,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.parent = parent
        self._owned = owned          # if True, cleanup() wipes the dir
        self._child_counter = 0
        self._write_manifest(manifest_meta or {})

    # ---------- path helpers ----------

    def safe_path(self, rel: str) -> Path:
        """Resolve `rel` under root, refusing to escape.

        Matches the guarantee hermes gives via its sandbox — a tool cannot
        `open("../../etc/passwd")` by way of a crafted path argument.
        """
        p = (self.root / rel).resolve()
        try:
            p.relative_to(self.root)
        except ValueError:
            raise ValueError(f"path escapes workspace: {rel}")
        return p

    def exists(self, rel: str) -> bool:
        try:
            return self.safe_path(rel).exists()
        except ValueError:
            return False

    # ---------- child workspaces (subagent isolation) ----------

    def child(self, name: str | None = None) -> "Workspace":
        """Carve out a subdirectory for a child agent.

        Each subagent gets its own sub-root so parallel children writing to
        `report.md` don't collide. Child workspaces always live under the
        parent root, so the parent can still `read_file("sub_0/report.md")`
        to aggregate results.

        Auto-naming probes for an unused `sub_N` so it never collides with an
        explicitly-named child created earlier.
        """
        if name is None:
            while True:
                candidate = f"sub_{self._child_counter}"
                self._child_counter += 1
                if not (self.root / candidate).exists():
                    name = candidate
                    break
        safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        child_root = self.root / safe_name
        return Workspace(
            child_root,
            manifest_meta={"role": "subagent", "name": safe_name},
            parent=self,
            owned=False,
        )

    # ---------- manifest ----------

    def _write_manifest(self, meta: dict[str, Any]) -> None:
        path = self.root / MANIFEST_NAME
        payload = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "pid": os.getpid(),
            "root": str(self.root),
            "parent": str(self.parent.root) if self.parent else None,
            **meta,
        }
        # Merge with existing manifest if present (e.g. Agent re-opens dir).
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    existing.update(payload)
                    payload = existing
            except Exception:
                pass
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            # A read-only workspace shouldn't crash Agent init.
            pass

    def manifest(self) -> dict[str, Any]:
        path = self.root / MANIFEST_NAME
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    # ---------- cleanup ----------

    def cleanup(self) -> None:
        """Remove the workspace directory if this workspace owns it.

        Only temp workspaces (created via `temp_workspace()`) are owned.
        Caller-supplied paths are never deleted.
        """
        if self._owned and self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    # ---------- context manager (for throwaway workspaces) ----------

    def __enter__(self) -> "Workspace":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.cleanup()


def temp_workspace(prefix: str = "run_") -> Workspace:
    """Create a throwaway workspace in the system temp dir.

    The returned Workspace owns its root and will delete it on `cleanup()` or
    when used as a context manager.
    """
    d = Path(tempfile.mkdtemp(prefix=prefix))
    return Workspace(d, manifest_meta={"ephemeral": True}, owned=True)
