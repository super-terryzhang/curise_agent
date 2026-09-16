"""Section 9 — Web API: ActionSpec schema-typed args (v42+).

测试目标：
    `ActionSpec.args_schema` 让 Tier-2 action 在 propose_action 写 DB
    行 *之前* 用 Pydantic 校验参数。这是 prod 2026-05-20 Action #8
    那个 bug 的结构性修复：之前 agent 漏传 target_id 时，DB 落了
    target_id=NULL 的行，dispatch 时才炸 BatchNotFound，UI 看到一个
    status=failed 行污染审批队列。新路径下：
      - schema 缺字段 → propose_action 返回 Error 字符串给 LLM 自纠
        (Temporal "Update Validator" 模式)
      - schema 通过 → DB 行 target_id 由 projection 自动算
      - legacy spec（args_schema=None）走原 (target_id, payload) 路径
      - LLM 既可传 target_id 又可塞 payload（兼容两种 prompt 风格）

为什么重要：
    没有 schema 校验，每次新 LLM 上线都得人肉测一遍 prompt-tool
    对齐。Anthropic 2026 paper 的 Shared Responsibility Model 明确说
    Tools 层必须自己验证，不能靠 Model 层的 prompt 安全。这条测试
    pin 住"propose 是 source of validation"这一架构边界。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, Field

from agent.runtime import approvals

# Side-effect import: registers every v3 business tool on REGISTRY,
# including propose_action + commit_upload_batch.
from agent.runtime import tools as _v3_tools  # noqa: F401
from agent.runtime.approvals import ActionSpec, register_action
from agent.runtime.deps import V3Deps, inject_deps
from agent.storage.models import ChatSession, PendingAction
from general_agent import REGISTRY, ToolContext


def _invoke_propose(ctx: ToolContext, **kwargs: Any) -> str:
    """Dispatch propose_action via the tool registry (the production
    code path). Keeps tests aligned with how the agent actually calls
    the tool — no shortcut access to internals."""
    return REGISTRY.view(["propose_action"]).dispatch(
        "propose_action", kwargs, ctx=ctx
    )


# ─── Fixtures ────────────────────────────────────────────────


@pytest.fixture
def _restore_dispatch():
    """Snapshot ACTION_DISPATCH and restore after each test."""
    snapshot = dict(approvals.ACTION_DISPATCH)
    yield
    approvals.ACTION_DISPATCH.clear()
    approvals.ACTION_DISPATCH.update(snapshot)


class _SampleArgs(BaseModel):
    """Tiny schema for testing — single required int field."""
    target_thing_id: int = Field(..., gt=0)
    note: str = ""


@pytest.fixture
def _registered_schema_spec(_restore_dispatch):
    """Register a schema-typed test action that mimics commit_upload_batch
    in shape (single required int + projection)."""
    captured: list[dict[str, Any]] = []

    def handler(deps, args: _SampleArgs) -> dict:  # noqa: ANN001
        captured.append({"id": args.target_thing_id, "note": args.note})
        return {"ok": True, "echoed": args.model_dump()}

    register_action(ActionSpec(
        name="sample_schema_action",
        target_kind="thing",
        description="test",
        dispatch=handler,
        args_schema=_SampleArgs,
        target_id_from_args=lambda a: a.target_thing_id,
    ))
    return captured


@pytest.fixture
def _registered_legacy_spec(_restore_dispatch):
    """Register a legacy (no-schema) action so the dual-path coexistence
    can be verified — neither spec interferes with the other."""
    captured: list[dict[str, Any]] = []

    def handler(deps, target_id, payload):  # noqa: ANN001
        captured.append({"target_id": target_id, "payload": payload})
        return {"legacy": True}

    register_action(ActionSpec(
        name="sample_legacy_action",
        target_kind="thing",
        description="test legacy",
        dispatch=handler,
    ))
    return captured


def _ctx_with_session(db, *, user_id: int) -> tuple[ToolContext, str]:
    """Build a ToolContext + create a ChatSession owned by user_id.
    The propose_action tool reads `ctx.agent.session_id` so we attach a
    minimal stub."""
    from domains.identity.models import User
    from infrastructure.security import hash_password
    if db.get(User, user_id) is None:
        db.add(User(id=user_id, email=f"schema-{user_id}@example.test", role="employee",
                    hashed_password=hash_password("password123"), is_active=True, is_default_password=False))
        db.commit()
    sid = f"sess-{user_id}-args-schema-{id(db)}"
    if db.get(ChatSession, sid) is None:
        db.add(ChatSession(id=sid, user_id=user_id, title="t"))
        db.commit()
    from pathlib import Path
    ctx = ToolContext(workspace=Path("/tmp"), extras={})
    inject_deps(ctx, V3Deps(db=db, user_id=user_id, user_role="employee"))

    class _AgentStub:
        session_id = sid
    ctx.agent = _AgentStub()  # type: ignore[attr-defined]
    return ctx, sid


# ─── Schema validation paths ─────────────────────────────────


def test_propose_schema_valid_payload_writes_row_with_projected_target_id(
    db, _registered_schema_spec
):
    """Happy path: Gemini-3-style call (only `payload`, no target_id).
    Validation passes, DB row's target_id is filled via projection."""
    ctx, sid = _ctx_with_session(db, user_id=1)
    out = _invoke_propose(ctx, action="sample_schema_action",
        summary="test",
        payload='{"target_thing_id": 42, "note": "hi"}',)
    assert "Approval request" in out, f"expected success, got: {out}"

    row = (
        db.query(PendingAction)
        .filter(PendingAction.session_id == sid)
        .order_by(PendingAction.id.desc())
        .first()
    )
    assert row is not None
    assert row.target_id == 42, "projection should have filled target_id"
    assert row.payload == {"target_thing_id": 42, "note": "hi"}
    assert row.status == "pending"


def test_propose_schema_legacy_target_id_param_still_works(
    db, _registered_schema_spec
):
    """Backward compat: legacy callers pass `target_id=42` with empty
    payload. The validator should splice the id into the schema's
    int field automatically."""
    ctx, sid = _ctx_with_session(db, user_id=1)
    out = _invoke_propose(ctx, action="sample_schema_action",
        summary="legacy style",
        target_id=42,
        payload='{"note": "from legacy"}',)
    assert "Approval request" in out

    row = (
        db.query(PendingAction)
        .filter(PendingAction.session_id == sid)
        .order_by(PendingAction.id.desc())
        .first()
    )
    assert row.target_id == 42
    assert row.payload["target_thing_id"] == 42
    assert row.payload["note"] == "from legacy"


def test_propose_schema_missing_field_returns_error_without_db_row(
    db, _registered_schema_spec
):
    """The bug the user hit: agent omits the required field entirely.
    propose should refuse, return a specific Error: string, and NOT
    create a status=failed row."""
    ctx, sid = _ctx_with_session(db, user_id=1)
    out = _invoke_propose(ctx, action="sample_schema_action",
        summary="missing id",
        payload='{"note": "no id"}',)
    assert out.startswith("Error:"), f"expected Error string, got: {out}"
    assert "target_thing_id" in out, "error must name the missing field"
    assert "payload" in out.lower(), "must explain where to put the field"

    # CRITICAL: no DB pollution
    rows = db.query(PendingAction).filter(PendingAction.session_id == sid).all()
    assert rows == [], "validation failure must not persist a row"


def test_propose_schema_type_mismatch_returns_error_without_db_row(
    db, _registered_schema_spec
):
    """Negative-int / wrong-type / etc. should reject."""
    ctx, sid = _ctx_with_session(db, user_id=1)
    out = _invoke_propose(
        ctx,
        action="sample_schema_action",
        summary="bad id",
        payload='{"target_thing_id": -5}',  # violates gt=0
    )
    assert out.startswith("Error:")
    assert "target_thing_id" in out
    assert db.query(PendingAction).filter(PendingAction.session_id == sid).count() == 0


def test_propose_schema_invalid_json_payload_returns_error(
    db, _registered_schema_spec
):
    """Malformed JSON should be caught before reaching the schema."""
    ctx, sid = _ctx_with_session(db, user_id=1)
    out = _invoke_propose(
        ctx,
        action="sample_schema_action",
        summary="bad json",
        payload='{"target_thing_id": 42',  # unterminated
    )
    assert out.startswith("Error:")
    assert "JSON" in out
    assert db.query(PendingAction).filter(PendingAction.session_id == sid).count() == 0


# ─── Legacy spec still works ─────────────────────────────────


def test_propose_legacy_spec_unaffected_by_schema_path(
    db, _registered_legacy_spec
):
    """Specs without args_schema take the legacy (target_id, payload)
    path. Adding the new feature must not regress them."""
    ctx, sid = _ctx_with_session(db, user_id=1)
    out = _invoke_propose(ctx, action="sample_legacy_action",
        summary="legacy test",
        target_id=7,
        payload='{"foo": "bar"}',)
    assert "Approval request" in out

    row = (
        db.query(PendingAction)
        .filter(PendingAction.session_id == sid)
        .order_by(PendingAction.id.desc())
        .first()
    )
    assert row.target_id == 7
    assert row.payload == {"foo": "bar"}


# ─── Real commit_upload_batch action (the prod failure) ──────


def test_propose_commit_upload_batch_gemini3_style_succeeds(db):
    """Production repro: Gemini 3 calls propose without target_id, only
    `payload='{"batch_id": N}'`. Before v42 this stored target_id=NULL
    and dispatch later failed with BatchNotFound. After v42 the schema
    parses batch_id from payload + projects it into target_id."""
    # Action already registered at import time — use it directly
    ctx, sid = _ctx_with_session(db, user_id=1)
    out = _invoke_propose(ctx, action="commit_upload_batch",
        summary="提交批次 11",
        payload='{"batch_id": 11}',)
    assert "Approval request" in out, out

    row = (
        db.query(PendingAction)
        .filter(PendingAction.session_id == sid)
        .order_by(PendingAction.id.desc())
        .first()
    )
    assert row.target_id == 11, (
        "v42 contract: schema projection must fill target_id from payload"
    )
    assert row.payload["batch_id"] == 11
    assert row.status == "pending"
