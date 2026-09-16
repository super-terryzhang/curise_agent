"""Agent memory = a single markdown file the agent can read/append.

Simple and human-editable. For richer needs swap this for a vector store
(the interface is `load()` + `append()` + `search()`).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path


class Memory:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("# Agent Memory\n\n", encoding="utf-8")

    def load(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def append(self, note: str, *, tag: str | None = None) -> None:
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        tag_str = f" [{tag}]" if tag else ""
        entry = f"\n## {stamp}{tag_str}\n\n{note.strip()}\n"
        with self.path.open("a", encoding="utf-8") as f:
            f.write(entry)

    def search(self, query: str, *, max_chars: int = 4000) -> str:
        """Naive case-insensitive substring search over memory."""
        content = self.load()
        q = query.lower().strip()
        if not q:
            return content[-max_chars:]
        hits: list[str] = []
        for block in content.split("\n## "):
            if q in block.lower():
                hits.append(("## " + block) if not block.startswith("# ") else block)
        if not hits:
            return "(no memory hits)"
        joined = "\n".join(hits)
        return joined[-max_chars:]
