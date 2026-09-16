"""HITL approval queue — the bridge between Tier-2 tools and the user.

A Tier-2 tool (delete, financial change, batch op) doesn't execute its
operation. Instead it writes a `PendingAction` row and emits an SSE
event so the frontend renders an approval card. When the user clicks
"approve", the HTTP `/decide` endpoint looks up the action in
`ACTION_DISPATCH` and runs the corresponding domain-service call.

The dispatch table is the single source of truth for "what action
names are valid". The `propose` tool's schema enumerates these so the
LLM can't make up unsafe action names — and the `/decide` endpoint
refuses unknown names too.

Why not let the agent call domain services directly?

1. Audit. Every Tier-2 op now has a row with `decided_by`, `decided_at`,
   `result`. Forensic-reconstructible.
2. Authorization. The decide endpoint re-checks user ownership at the
   point of execution, so a session hijack between propose and decide
   still can't reach across users.
3. UX. The frontend can render a structured card from `payload`, not
   parse natural-language assistant messages.
4. Recovery from prompt-injection. If a doc the agent reads contains
   "DELETE ALL ORDERS", the worst case is a pending row the user
   immediately rejects.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session as SqlaSession

from agent.runtime.deps import V3Deps
from agent.storage.models import PendingAction


@dataclass(frozen=True)
class ActionSpec:
    """Specification for a Tier-2 action.

    Two contract styles are supported (legacy + schema-typed) to allow
    incremental migration without breaking the 6 actions that still use
    the historical `(target_id, payload)` shape:

    - **Legacy**: `args_schema` is None. `dispatch(deps, target_id,
      payload)` receives the raw DB columns. Caller is responsible for
      not passing nonsense (validation only happens via the underlying
      domain service, possibly producing a `failed` approval row when
      the agent omitted a required field).

    - **Schema-typed** (industry pattern — LangGraph / OpenAI / Anthropic
      / Temporal all converge here): `args_schema` is a Pydantic model
      describing the action's arguments. `propose_action` validates
      against it BEFORE persisting, so a missing/malformed field surfaces
      as a structured Error string the agent can self-correct from
      (Temporal "Update Validator" pattern), instead of becoming a
      `status=failed` row that pollutes the approval queue.

      In this mode `dispatch(deps, args)` receives the parsed typed model.
      `target_id_from_args` is used to project a scalar `target_id` value
      into the DB row for indexing/display only — args is the source of
      truth, target_id is denormalised.

    Migration: register a spec without `args_schema` to keep legacy; add
    it later when you're ready. Both styles coexist in `ACTION_DISPATCH`.
    """

    name: str
    target_kind: str
    description: str
    dispatch: Callable[..., dict[str, Any]]
    # Optional Pydantic schema; presence flips this spec into schema-typed
    # mode. Field comparisons must remain hashable for `register_action`'s
    # idempotency check — Pydantic model classes are hashable by identity,
    # callables likewise.
    args_schema: type[BaseModel] | None = None
    # Project a scalar id from validated args. Falls back to `None` if
    # absent; `target_id` then stays NULL in DB (only matters for UI
    # filters that group by target). Example for commit_upload_batch:
    # `lambda a: a.batch_id`.
    target_id_from_args: Callable[[BaseModel], int | None] | None = None


# ─── Dispatch registry ──────────────────────────────────────────────
#
# Action handlers are registered lazily by the modules that need them
# (e.g. agent/runtime/tools/orders.py registers "delete_order" the first
# time it's imported). This avoids circular imports between the runtime
# layer and the domain services.
ACTION_DISPATCH: dict[str, ActionSpec] = {}


def register_action(spec: ActionSpec) -> None:
    """Register a Tier-2 action. Idempotent — re-registering with the
    same spec is a no-op so re-imports during tests don't trip."""
    existing = ACTION_DISPATCH.get(spec.name)
    if existing is not None and existing == spec:
        return
    ACTION_DISPATCH[spec.name] = spec


def known_action_names() -> list[str]:
    """For tool schema enumeration — the LLM only sees these names."""
    return sorted(ACTION_DISPATCH.keys())


def get_spec(action: str) -> ActionSpec | None:
    return ACTION_DISPATCH.get(action)


# ─── Pending row CRUD (used by tool + HTTP layer) ───────────────────


def create_pending(
    db: SqlaSession,
    *,
    session_id: str,
    user_id: int,
    action: str,
    target_kind: str,
    target_id: int | None,
    payload: dict[str, Any],
    summary: str,
) -> PendingAction:
    payload = dict(payload or {})
    if action == "update_product_financial" or (action == "delete_masterdata" and payload.get("entity") == "products"):
        from domains.masterdata.price_history import lock_product
        product = lock_product(db, target_id)
        # Capture server state at proposal time, never trust a model-supplied revision.
        payload["expected_revision"] = product.revision
    row = PendingAction(
        session_id=session_id,
        user_id=user_id,
        action=action,
        target_kind=target_kind,
        target_id=target_id,
        payload=payload or {},
        summary=summary,
        status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def fetch_pending(db: SqlaSession, action_id: int, *, user_id: int) -> PendingAction | None:
    """User-scoped fetch. Returns None if action belongs to another user
    or doesn't exist. The decide endpoint must use this — never `db.get`
    directly — so cross-user approval attempts cleanly 404."""
    row = db.get(PendingAction, action_id)
    if row is None or row.user_id != user_id:
        return None
    return row


def mark_decided(
    db: SqlaSession,
    row: PendingAction,
    *,
    status: str,
    decided_by: int,
    result: dict[str, Any] | None = None,
) -> None:
    row.status = status
    row.decided_at = datetime.utcnow()
    row.decided_by = decided_by
    row.result = result
    db.commit()
