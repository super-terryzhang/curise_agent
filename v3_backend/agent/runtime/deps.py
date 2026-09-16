"""V3Deps — strongly-typed business context for v3 agent tools.

Mirrors Pydantic AI / OpenAI Agents SDK's typed-deps pattern, but bridges
through general-agent's `ToolContext.extras: dict[str, Any]` so we don't
have to fork general-agent's ToolContext into a generic.

Usage in a tool::

    from general_agent import tool, ToolContext
    from agent.runtime import V3Deps, get_deps

    @tool(toolset="orders")
    def query_db(sql: str, *, ctx: ToolContext) -> str:
        deps = get_deps(ctx)            # raises if not injected
        rows = deps.db.execute(text(sql))
        return ...

To inject from chat HTTP::

    agent = Agent(AgentConfig(...))
    agent.ctx.extras["v3_deps"] = V3Deps(db=db, user_id=user.id, user_role=user.role)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Single key used inside ctx.extras — namespaced to avoid colliding with
# general-agent's own extras (e.g. "workspace_obj", "cancel_token").
_EXTRAS_KEY = "v3_deps"


@dataclass
class V3Deps:
    """Business context every v3 tool gets through `ctx.deps`.

    Add fields here as the tool surface grows (tenant_id, trace_id, ...).
    Each field is type-annotated so mypy / IDE autocomplete works in tools.
    """

    db: Any  # SQLAlchemy Session — typed Any to keep this module SQLAlchemy-free
    user_id: int
    user_role: str = "employee"
    auth_session_id: str | None = None

    def __post_init__(self) -> None:
        if self.auth_session_id is None:
            info = getattr(self.db, "info", None)
            if isinstance(info, dict):
                self.auth_session_id = info.get("auth_session_id")


class DepsNotInjectedError(RuntimeError):
    """Raised when a v3 tool is called without V3Deps in the agent context.

    This is a wiring bug, not a runtime condition — the agent factory MUST
    set `ctx.extras["v3_deps"]` before any business tool runs.
    """


def get_deps(ctx: Any) -> V3Deps:
    """Unwrap V3Deps from a general-agent ToolContext.

    `ctx` is `general_agent.ToolContext` but we type it as Any because we
    don't want to import general-agent at v3 module load time (the import
    is part of the chat HTTP path, not every domain module).
    """
    extras = getattr(ctx, "extras", None)
    if not isinstance(extras, dict):
        raise DepsNotInjectedError(
            "ToolContext.extras is not a dict — agent factory misconfigured"
        )
    deps = extras.get(_EXTRAS_KEY)
    if not isinstance(deps, V3Deps):
        raise DepsNotInjectedError(
            f"V3Deps not found in ctx.extras[{_EXTRAS_KEY!r}] — "
            "agent factory must set this before running any v3 tool"
        )
    from domains.identity import service as identity_service

    if deps.auth_session_id is not None:
        identity_service.assert_session_active(deps.db, deps.user_id, deps.auth_session_id)
    user = identity_service.get_business_user(deps.db, deps.user_id)
    deps.user_role = user.role
    return deps


def inject_deps(ctx: Any, deps: V3Deps) -> None:
    """Set V3Deps on a ToolContext. Called by the agent factory before run()."""
    extras = getattr(ctx, "extras", None)
    if not isinstance(extras, dict):
        raise DepsNotInjectedError("ToolContext.extras is not a dict")
    extras[_EXTRAS_KEY] = deps
