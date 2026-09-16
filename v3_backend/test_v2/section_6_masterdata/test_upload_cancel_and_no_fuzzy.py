"""Section 6 — masterdata: cancel_batch + fuzzy-match removal contract.

测试目标：
    Pin two related contracts introduced 2026-05-25:

    1. **No fuzzy similarity matching.** Real prod cases (collected from
       customer screenshots): `LETTUCE MIZUNA` was 0.667-similar to
       `LETTUCE MESCLUN MIX`, `APPLE FUJI 113CT/40LB` 0.609 to
       `ORANGE JUICING 100CT/40LB`. Old behaviour silently treated those
       as updates. New behaviour: anything without an explicit code/name
       match goes to `new`.

    2. **cancel_batch is distinct from rollback_batch.**
       - `cancel_batch` works on `status=resolved` (user declined before
         commit). No changelog needed; just mark cancelled.
       - `rollback_batch` works on `status=completed` (undo a write via
         changelog). The old code conflated the two — declining at the
         HITL gate tried to rollback an uncommitted batch and crashed
         with "rollback requires status=completed, got resolved".

为什么必须有：
    Without these tests, anyone re-introducing a similarity threshold or
    overloading rollback to also accept `resolved` re-creates the prod
    bug pattern (customer mistakenly updates wrong SKU / gets stuck in
    HITL state machine with no escape).
"""

from __future__ import annotations

import pytest

from domains.masterdata.upload import service as upload
from domains.masterdata.upload.models import StagingProduct, UploadBatch
from domains.masterdata.upload.service import BatchInWrongState


# ─── shared helpers ────────────────────────────────────────


def _seed_product(db, *, code: str | None, name: str) -> None:
    """Insert one Product. Uses the shared `seed_product` helper so the
    strict identity contract (auto-default country+port) applies."""
    from test_v2.fixtures.helpers import seed_product as _shared_seed

    _shared_seed(db, code=code or "TEST-001", name=name)


def _resolved_batch(db, *, rows: list[dict]) -> UploadBatch:
    """Use the shared `make_excel` so default country/port get injected."""
    from test_v2.fixtures.helpers import make_excel

    blob = make_excel(rows)
    b = upload.parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=1)
    upload.resolve_and_score(db, batch_id=b.id, user_id=1)
    db.refresh(b)
    return b


# ─── 1) No fuzzy: real prod cases must land as `new` ───────


def test_no_fuzzy_lettuce_mizuna_creates_new_row(db):
    """Customer-reported case: `LETTUCE MIZUNA` against existing
    `LETTUCE MESCLUN MIX`. SequenceMatcher.ratio() returns 0.667, which
    used to trigger fuzzy and silently update mesclun. Must be `new`."""
    _seed_product(db, code="L1", name="LETTUCE MESCLUN MIX")
    batch = _resolved_batch(db, rows=[{"product_name": "LETTUCE MIZUNA"}])
    sp = db.query(StagingProduct).filter_by(batch_id=batch.id).one()
    assert sp.match_status == "new"
    assert sp.match_target_id is None


def test_no_fuzzy_apple_vs_orange_creates_new_row(db):
    """Customer-reported case: `APPLE FUJI 113CT/40LB` ~0.609 similar to
    `ORANGE JUICING 100CT/40LB` (shared CT/LB tokens). Different fruit
    entirely — never auto-match."""
    _seed_product(db, code="O1", name="ORANGE JUICING 100CT/40LB")
    batch = _resolved_batch(
        db, rows=[{"product_name": "APPLE FUJI 113CT/40LB"}]
    )
    sp = db.query(StagingProduct).filter_by(batch_id=batch.id).one()
    assert sp.match_status == "new"
    assert sp.match_target_id is None


# ─── 2) cancel_batch happy path ────────────────────────────


def test_cancel_batch_marks_resolved_batch_cancelled(db):
    """Resolved batch + cancel → status flips to `cancelled`. Staging rows
    are intentionally retained (audit / 'what did I almost upload?')."""
    _seed_product(db, code="A1", name="Apple")
    batch = _resolved_batch(db, rows=[{"product_name": "Brand New SKU"}])
    assert batch.status == "resolved"

    result = upload.cancel_batch(db, batch_id=batch.id, user_id=1)
    assert result["ok"] is True
    assert result["status"] == "cancelled"
    assert result["already"] is False

    db.refresh(batch)
    assert batch.status == "cancelled"
    # Staging rows kept for audit
    staging_count = db.query(StagingProduct).filter_by(batch_id=batch.id).count()
    assert staging_count == 1


def test_cancel_batch_is_idempotent(db):
    """Calling cancel twice on the same batch must return `already=True`
    on the second call, not crash. Customers double-click."""
    batch = _resolved_batch(db, rows=[{"product_name": "X"}])
    upload.cancel_batch(db, batch_id=batch.id, user_id=1)
    result = upload.cancel_batch(db, batch_id=batch.id, user_id=1)
    assert result["already"] is True
    assert result["status"] == "cancelled"


# ─── 3) Error boundaries: clearly tell user which verb to use ──


def test_cancel_batch_rejects_completed_with_helpful_message(db):
    """cancel on a committed batch → BadRequest pointing to rollback."""
    batch = _resolved_batch(db, rows=[{"product_name": "X"}])
    upload.commit_batch(db, batch_id=batch.id, user_id=1)
    db.refresh(batch)
    assert batch.status == "completed"

    with pytest.raises(BatchInWrongState) as exc_info:
        upload.cancel_batch(db, batch_id=batch.id, user_id=1)
    msg = str(exc_info.value)
    assert "resolved" in msg
    assert "rollback" in msg, "error must direct user to the correct verb"


def test_rollback_batch_rejects_resolved_with_helpful_message(db):
    """rollback on a not-yet-committed batch → BadRequest pointing to cancel.
    This was the prod bug: customer saw the raw English error and got stuck."""
    batch = _resolved_batch(db, rows=[{"product_name": "X"}])
    assert batch.status == "resolved"

    with pytest.raises(BatchInWrongState) as exc_info:
        upload.rollback_batch(db, batch_id=batch.id, user_id=1)
    msg = str(exc_info.value)
    assert "cancel" in msg
    # The message also keeps the technical state for engineers grepping logs
    assert "resolved" in msg


# ─── 4) HITL action registration ───────────────────────────


def test_cancel_upload_batch_action_is_registered():
    """The agent runtime must expose `cancel_upload_batch` so SKILL.md's
    `propose_action(action="cancel_upload_batch", ...)` resolves at
    dispatch time. Without this, the agent's HITL proposal silently
    falls back to the legacy rollback path."""
    # Import the side-effecting module that registers actions
    from agent.runtime import approvals as _approvals_mod  # noqa: F401
    from agent.runtime.tools import data_upload as _du  # noqa: F401
    from agent.runtime.approvals import get_spec

    spec = get_spec("cancel_upload_batch")
    assert spec is not None, (
        "cancel_upload_batch action must be registered. If this fails, "
        "tools.data_upload didn't run its register_action() block."
    )
    # The cancel spec must reuse the same schema as commit/rollback so
    # callers can pass batch_id via either `target_id` (legacy) or
    # `payload.batch_id` (canonical).
    assert spec.args_schema is not None


def test_cancel_batch_is_re_exported_from_package():
    """The HITL dispatcher calls `upload_service.cancel_batch(...)` where
    `upload_service` is the package `domains.masterdata.upload` (see
    `agent/runtime/tools/data_upload.py:27`). Adding a new function to
    `service.py` WITHOUT re-exporting it in `__init__.py` produces a prod
    AttributeError at dispatch time ("module ... has no attribute
    'cancel_batch'") — exactly the v57→v58 regression we hit on 2026-05-26.

    Pin every public-surface name listed in the docstring so re-exports
    can't drift again."""
    from domains.masterdata import upload as upload_pkg

    required_exports = (
        "parse_excel",
        "resolve_and_score",
        "preview_changes",
        "commit_batch",
        "cancel_batch",
        "rollback_batch",
        "list_batches",
        "search_batches",
        "get_batch",
        "inspect_row",
        "UploadError",
    )
    for name in required_exports:
        assert hasattr(upload_pkg, name), (
            f"domains.masterdata.upload package missing `{name}` — "
            f"add to __init__.py imports + __all__"
        )
