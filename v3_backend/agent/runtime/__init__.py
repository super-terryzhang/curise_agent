"""v3 integration layer for general-agent.

Holds v3-specific extensions of general-agent's pluggable points:

- `V3Deps`: strongly-typed business context (db / user_id / user_role)
  passed through `ToolContext.extras` and unwrapped by `get_deps()` at the
  start of every business tool. Mirrors OpenAI Agents SDK's
  `RunContextWrapper[T]` / Pydantic AI's `RunContext[Deps]` in spirit, but
  uses the existing `extras` dict so we don't fork general-agent's
  ToolContext.
- `V3SessionStore`: SQLAlchemy-backed implementation that duck-types
  general-agent's `SessionStore` interface. Persists into v3's
  `v3_chat_sessions` / `v3_chat_messages` tables (Postgres in prod,
  SQLite in dev). User-scoped at construction time so cross-user data
  leakage is impossible by design.
- `V3MemoryStore`: ditto for `v3_agent_memories`.
"""

from agent.runtime.deps import V3Deps, get_deps, inject_deps
from agent.runtime.factory import create_v3_chat_agent
from agent.runtime.llm import default_chat_llm_config
from agent.runtime.memory_adapter import V3Memory
from agent.runtime.session_store import V3SessionStore

__all__ = [
    "V3Deps",
    "V3Memory",
    "V3SessionStore",
    "create_v3_chat_agent",
    "default_chat_llm_config",
    "get_deps",
    "inject_deps",
]
