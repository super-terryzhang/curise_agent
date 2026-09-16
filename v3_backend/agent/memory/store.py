"""MemoryStore — long-term memory CRUD.

User-scoped at construction time. Memories are typed (`user_preference`,
`supplier_knowledge`, `workflow_pattern`, `fact`) and uniquely keyed
within `(user_id, memory_type, key)` so re-extracting the same fact
updates the value rather than duplicating.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as SqlaSession

from agent.storage.models import AgentMemory


@dataclass
class MemoryEntry:
    id: int
    memory_type: str
    key: str
    value: str
    access_count: int
    last_accessed_at: datetime | None
    source_session_id: str | None


class MemoryStore:
    """User-scoped CRUD over `v3_agent_memories`."""

    def __init__(self, db: SqlaSession, *, user_id: int):
        self._db = db
        self._user_id = user_id

    # ─── Read ────────────────────────────────────────────────

    def list(
        self, *, memory_type: str | None = None, limit: int | None = None
    ) -> list[MemoryEntry]:
        stmt = (
            select(AgentMemory)
            .where(AgentMemory.user_id == self._user_id)
            .order_by(AgentMemory.access_count.desc(), AgentMemory.updated_at.desc())
        )
        if memory_type is not None:
            stmt = stmt.where(AgentMemory.memory_type == memory_type)
        if limit is not None:
            stmt = stmt.limit(limit)
        return [_to_entry(r) for r in self._db.execute(stmt).scalars()]

    def get(self, *, memory_type: str, key: str) -> MemoryEntry | None:
        row = self._fetch(memory_type=memory_type, key=key)
        return _to_entry(row) if row else None

    # ─── Write ───────────────────────────────────────────────

    def upsert(
        self,
        *,
        memory_type: str,
        key: str,
        value: str,
        source_session_id: str | None = None,
    ) -> MemoryEntry:
        """Insert or update a memory entry.

        Hits the same row on `(user_id, memory_type, key)`. Returns the
        persisted entry (post-flush so `id` is set).
        """
        existing = self._fetch(memory_type=memory_type, key=key)
        if existing is None:
            row = AgentMemory(
                user_id=self._user_id,
                memory_type=memory_type,
                key=key,
                value=value,
                source_session_id=source_session_id,
            )
            self._db.add(row)
            self._db.flush()
            return _to_entry(row)
        existing.value = value
        if source_session_id is not None:
            existing.source_session_id = source_session_id
        existing.updated_at = datetime.utcnow()
        self._db.flush()
        return _to_entry(existing)

    def touch(self, entry_id: int) -> None:
        """Bump `access_count` + `last_accessed_at` when a memory is used."""
        row = self._db.get(AgentMemory, entry_id)
        if row is None or row.user_id != self._user_id:
            return
        row.access_count = (row.access_count or 0) + 1
        row.last_accessed_at = datetime.utcnow()
        self._db.flush()

    def delete(self, *, memory_type: str, key: str) -> bool:
        row = self._fetch(memory_type=memory_type, key=key)
        if row is None:
            return False
        self._db.delete(row)
        self._db.flush()
        return True

    # ─── Helpers ─────────────────────────────────────────────

    def _fetch(self, *, memory_type: str, key: str) -> AgentMemory | None:
        stmt = select(AgentMemory).where(
            AgentMemory.user_id == self._user_id,
            AgentMemory.memory_type == memory_type,
            AgentMemory.key == key,
        )
        return self._db.execute(stmt).scalar_one_or_none()


def _to_entry(row: AgentMemory) -> MemoryEntry:
    return MemoryEntry(
        id=row.id,
        memory_type=row.memory_type,
        key=row.key,
        value=row.value,
        access_count=row.access_count or 0,
        last_accessed_at=row.last_accessed_at,
        source_session_id=row.source_session_id,
    )
