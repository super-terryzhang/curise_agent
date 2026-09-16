"""Cross-session agent memory — whitelisted to use `db.*` (ADR-0006).

`MemoryStore` is a typed/keyed CRUD wrapper around `v3_agent_memories`.
The chat agent reaches it via `agent.runtime.memory_adapter.V3Memory`,
which adapts this CRUD surface to general-agent's load/append/search
`Memory` interface.
"""

from agent.memory.store import MemoryEntry, MemoryStore

__all__ = ["MemoryStore", "MemoryEntry"]
