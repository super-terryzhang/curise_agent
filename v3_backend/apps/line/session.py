"""SessionSource — uniform "where did this message come from" descriptor.

Inspired by hermes-agent's `gateway/session.py` but trimmed to what v3
actually needs: just enough fields to pick the right reply target and
inject context into the system prompt. Adding a new platform later (e.g.
Telegram) means another place that builds a `SessionSource` — the
agent-side code that consumes it doesn't change.

Idle-timeout policy lives here too so the v2 chat router can ignore it
and the LINE handler picks it up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# How long after the last activity we close a chat session and start a
# fresh one for the same LINE user. Mirrors v2's behavior so cross-day
# conversations don't accumulate stale context.
SESSION_IDLE_TIMEOUT_MINUTES: int = 30

# Reset keywords — sender types one of these to force a new session.
# Group chats deliberately ignore this (group context is shared, one user
# shouldn't be able to wipe everyone else's state).
RESET_KEYWORDS: frozenset[str] = frozenset(
    {"新对话", "重置", "新建对话", "reset"}
)


@dataclass(frozen=True)
class SessionSource:
    """Where a LINE event came from.

    `chat_type` distinguishes DM (`"dm"`) from group (`"group"`). For groups,
    `chat_id` is the group_id and `user_id` is the sender's userId — both
    are needed to (a) reply to the group and (b) look up the sender's
    binding for permission checks.

    `platform_user_key` is what we use to dedup / look up bindings — it's
    always the *individual sender's* LINE userId (NOT the group_id), because
    permissions are per-individual (see v3_line_integration_decisions.md).
    """

    platform: str  # "line" — keep stringly-typed for now; promote to enum if we add another platform
    channel_id: str
    chat_id: str  # DM: same as user_id. Group: group_id.
    chat_type: str  # "dm" | "group"
    user_id: str  # always the individual LINE userId (sender)

    @property
    def platform_user_key(self) -> str:
        """The key we look up bindings by — always the individual sender."""
        return self.user_id

    @property
    def is_group(self) -> bool:
        return self.chat_type == "group"


def is_session_idle(last_activity: datetime | None, *, now: datetime | None = None) -> bool:
    """True if a session's last activity is older than the idle threshold.

    `last_activity=None` is treated as idle — fresh sessions never have it
    set yet, but they shouldn't pass through this guard anyway (caller
    only invokes this for resumed sessions).
    """
    if last_activity is None:
        return True
    cutoff = (now or datetime.utcnow()) - timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)
    return last_activity < cutoff


def is_reset_keyword(text: str) -> bool:
    """True if the user typed something we should treat as 'start fresh'."""
    return text.strip().lower() in {k.lower() for k in RESET_KEYWORDS} or text.strip() in RESET_KEYWORDS
