"""ORM models for chat persistence — whitelisted to touch `db.*` (ADR-0006).

Tables:
- `v3_chat_sessions` — one row per chat session.
- `v3_chat_messages` — one row per message; parts stored as JSON.

The actual session-store implementation that writes to these tables
lives in `agent.runtime.session_store.V3SessionStore` (also on the
ADR-0006 RULE-2 whitelist). This module just exports the ORM rows.
"""

from agent.storage.models import AgentMemory, ChatMessage, ChatSession

__all__ = ["AgentMemory", "ChatSession", "ChatMessage"]
