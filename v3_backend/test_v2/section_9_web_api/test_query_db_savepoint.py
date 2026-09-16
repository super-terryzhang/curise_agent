"""Section 9 — query_db must not poison the session on bad SQL.

Real prod incident 2026-05-18 (session 96fb84bdbcd945a9a0a672ace46b6ee6):
    user uploaded → committed → agent in followup turn called `query_db`
    with bad SQL (likely a v2_-prefixed table that doesn't apply to v3
    data). Postgres rejected → connection entered "current transaction is
    aborted" state. `query_db` caught the exception but DID NOT rollback,
    so the next thing the agent did (V3SessionStore.append → SELECT
    v3_chat_sessions) crashed with `InFailedSqlTransaction`. The whole
    followup chain blew up; user saw a long Python traceback in chat.

Fix (verified against SQLAlchemy 2.0 canonical pattern + LangChain SQL
agent best practices + HuggingFace SmolAgents text-to-SQL cookbook —
all four sources converge):
    wrap the `deps.db.execute(text(sql))` in `with deps.db.begin_nested():`
    so the SAVEPOINT auto-rolls back on exception, leaving the outer
    transaction (the agent loop's session) usable for subsequent ops.

These tests are deterministic, hit a real in-memory SQLite, and verify:
    (1) Bad SQL still returns the "Error: ..." string (LLM retry layer)
    (2) AFTER the bad SQL, the SAME session can still execute other ORM
        operations without InFailedSqlTransaction / PendingRollbackError
    (3) Good SQL still returns rows (no regression)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from agent.runtime.deps import V3Deps, inject_deps
from agent.runtime.tools.query_db import query_db
from agent.storage.models import ChatSession
from general_agent import ToolContext


def _ctx(db, *, user_id: int) -> ToolContext:
    ctx = ToolContext(workspace=Path("/tmp"), extras={})
    inject_deps(ctx, V3Deps(db=db, user_id=user_id, user_role="superadmin"))
    return ctx


# ─── Core regression: bad SQL must not poison the session ─────


def test_bad_sql_returns_error_string_not_exception(db, seed_user):
    """LangChain pattern: bad SQL → return error string to LLM, do NOT
    let the exception escape (LLM uses the string to rewrite the SQL)."""
    out = query_db("SELECT * FROM table_that_does_not_exist", ctx=_ctx(db, user_id=seed_user.id))
    assert out.startswith("Error:"), f"expected Error string, got: {out!r}"


def test_bad_sql_does_not_poison_outer_session(db, seed_user):
    """The bug we're fixing: after bad SQL, the SAME session must still
    be usable. Without savepoint wrap, this raises InFailedSqlTransaction
    on PostgreSQL or PendingRollbackError on SQLAlchemy session state."""
    # Step 1: deliberately bad SQL
    bad_out = query_db(
        "SELECT * FROM table_that_does_not_exist_xyz",
        ctx=_ctx(db, user_id=seed_user.id),
    )
    assert bad_out.startswith("Error:"), bad_out

    # Step 2: SAME session must still work for legitimate ORM ops.
    # This is exactly what the agent loop does (V3SessionStore reads
    # ChatSession AFTER a tool call) — and what crashed in prod.
    # We don't need a real chat session row; the SELECT itself is the
    # canary. If session is poisoned, this raises.
    db.execute(text("SELECT 1")).scalar()       # raw SQL canary
    db.get(ChatSession, "nonexistent-id")        # ORM canary (the one that crashed in prod)


def test_good_sql_still_works(db, seed_user):
    """No regression — well-formed SELECT returns JSON with columns/rows."""
    import json
    out = query_db("SELECT 1 AS one, 'hi' AS greeting", ctx=_ctx(db, user_id=seed_user.id))
    payload = json.loads(out)
    assert payload["columns"] == ["one", "greeting"]
    assert payload["rows"] == [{"one": 1, "greeting": "hi"}]


def test_multiple_bad_then_good_in_same_session(db, seed_user):
    """Stress: 3 bad queries in a row, then a good one — all must work
    on the SAME session without poisoning. Verifies savepoint releases
    cleanly even on repeated failures."""
    ctx = _ctx(db, user_id=seed_user.id)
    import json

    for _ in range(3):
        out = query_db("SELECT bogus FROM nowhere", ctx=ctx)
        assert out.startswith("Error:")

    # Same session, same ctx — must still answer a good query.
    out = query_db("SELECT 42 AS answer", ctx=ctx)
    payload = json.loads(out)
    assert payload["rows"] == [{"answer": 42}]


def test_bad_sql_followed_by_orm_get_chat_session_works(db, seed_user):
    """Reproduce the EXACT prod failure chain: bad query_db → then
    V3SessionStore-style `db.get(ChatSession, id)`. This is what crashed
    on prod session 96fb84bdbcd945a9a0a672ace46b6ee6."""
    # Seed a real chat session row so the SELECT actually has something
    # to find / not find — exercises the same code path V3Session
    # Store._owns() hits.
    sess = ChatSession(
        id="test-savepoint-recovery",
        user_id=seed_user.id,
        title="bug repro",
        status="active",
    )
    db.add(sess)
    db.commit()

    # Now: bad SQL via query_db, then ORM read of the chat session.
    bad = query_db(
        "SELECT not_a_column FROM ghost_table",
        ctx=_ctx(db, user_id=seed_user.id),
    )
    assert bad.startswith("Error:")

    # Without savepoint fix → InFailedSqlTransaction here.
    found = db.get(ChatSession, "test-savepoint-recovery")
    assert found is not None and found.title == "bug repro"
