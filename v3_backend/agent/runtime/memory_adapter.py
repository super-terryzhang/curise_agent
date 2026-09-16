"""V3Memory — DB-backed memory that duck-types general-agent's `Memory`.

general-agent's default `Memory` writes to a markdown file under the
agent's workspace. v3 needs cross-session, user-scoped memory persisted
into Postgres so that:

1. The same user's memories survive across HTTP requests, container
   restarts, and pod replacements.
2. Different users can't read each other's memories — enforced at the
   storage layer, not by trusting the LLM.
3. Memory rows are queryable by SQL (admin auditing, debugging, future
   analytics) — markdown blobs aren't.

Surface (matches `agent.memory.Memory` so `ctx.memory.append()` /
`ctx.memory.search()` from general-agent's `notes` toolkit Just Work):

  - `append(note, *, tag=None)` — store a note under tag (default "note")
  - `search(query, *, max_chars=N)` — substring match, returns markdown
  - `load()` — concatenate all entries into a markdown blob

Internally we delegate to v3's existing `MemoryStore` so the storage
schema stays single-source-of-truth: `agent/memory/store.py` owns the
ORM, this adapter only translates the interface.

Each `append` creates a new row with a unique key derived from a
monotonic timestamp + nanosecond suffix, so concurrent appends from
the same agent run don't collide on the `(user_id, memory_type, key)`
unique index.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as SqlaSession

from agent.memory.store import MemoryStore

_DEFAULT_TAG = "note"
_MAX_SEARCH_CHARS = 4000


class V3Memory:
    """User-scoped memory for general-agent's chat runtime."""

    def __init__(self, db: SqlaSession, *, user_id: int) -> None:
        self._store = MemoryStore(db, user_id=user_id)
        self._user_id = user_id

    # ─── general-agent Memory surface ────────────────────────

    def append(self, note: str, *, tag: str | None = None) -> None:
        if not note or not note.strip():
            return
        memory_type = (tag or _DEFAULT_TAG).strip() or _DEFAULT_TAG
        # Key must be unique within (user_id, memory_type) to avoid the
        # upsert path overwriting prior notes. uuid4 gives 122 bits of
        # collision resistance; concatenating timestamp lets a human
        # reading the DB sort by recency without joining other columns.
        stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        key = f"{stamp}-{uuid.uuid4().hex[:12]}"
        self._store.upsert(memory_type=memory_type, key=key, value=note.strip())

    def load(self) -> str:
        """Return all memories as a single markdown document.

        Format mirrors the on-disk Memory:
            # Agent Memory

            ## YYYY-MM-DD HH:MM [tag]

            <note>
        """
        entries = self._store.list()
        if not entries:
            return "# Agent Memory\n\n"
        parts: list[str] = ["# Agent Memory\n"]
        for e in entries:
            stamp = (
                e.last_accessed_at.strftime("%Y-%m-%d %H:%M")
                if e.last_accessed_at
                else "unknown-time"
            )
            tag_str = f" [{e.memory_type}]" if e.memory_type else ""
            parts.append(f"\n## {stamp}{tag_str}\n\n{e.value.strip()}\n")
        return "".join(parts)

    def search(self, query: str, *, max_chars: int = _MAX_SEARCH_CHARS) -> str:
        """Naive case-insensitive substring search; returns markdown blocks.

        Matches general-agent's Memory.search semantics: empty query →
        tail of memory; otherwise filter blocks containing the query.
        """
        content = self.load()
        q = (query or "").strip().lower()
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
