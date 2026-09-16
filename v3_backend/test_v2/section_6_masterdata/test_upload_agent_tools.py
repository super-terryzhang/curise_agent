"""Section 6 — Masterdata upload pipeline, Tool layer.

测试目标：
    Stages 1-5 service 都有 unit 测试覆盖；这个文件测的是**agent 入口** —
    `list_my_uploads` / `parse_uploaded_file` / `preview_upload` 三个 @tool
    函数。它们是 service 的"薄包装"，但薄包装本身可能漏：
      - 跨用户隔离（Bob 拿 batch_id=Alice's → 友好错而非 500）
      - 状态机错误转发（在 status=parsing 时调 parse_uploaded_file 应被服务
        层 BatchInWrongState 拦下，工具应把它包成 "Error: ..." 字符串）
      - 工具返回 JSON 还是字符串错误（agent 解析时会按字符串前缀 "Error:" 走错误路径）
      - user_id 真的从 V3Deps 里取，而不是从参数里被诱导覆盖

为什么重要：
    工具是 agent 与 service 唯一的接触面。返回类型不对或者用户隔离掉到
    service 之外，agent 会暴露其它用户的数据 / 在错误状态机里跑死循环。

设计方法：
    REGISTRY.view(...).dispatch(...) 直接走真实工具链路；
    ctx 由 V3Deps 注入；
    断言 JSON / "Error:" 前缀 + DB 状态。
"""

from __future__ import annotations

import json
from pathlib import Path

from general_agent import REGISTRY, ToolContext

# Side-effect import: registers every v3 business tool on REGISTRY.
from agent.runtime import tools as _v3_tools  # noqa: F401
from agent.runtime.deps import V3Deps, inject_deps
from domains.masterdata.upload import parse_excel, resolve_and_score
from test_v2.fixtures.helpers import make_excel


def _ctx_with_deps(db, *, user_id: int, role: str = "employee") -> ToolContext:
    from domains.identity.models import User
    from test_v2.fixtures.helpers import seed_user

    if db.get(User, user_id) is None:
        user = seed_user(db, email=f"upload-{user_id}@example.com", role=role)
        user.id = user_id
        db.commit()
    ctx = ToolContext(workspace=Path("/tmp"), extras={})
    inject_deps(ctx, V3Deps(db=db, user_id=user_id, user_role=role))
    return ctx


def _seed_batch(db, *, user_id: int, filename: str, names: list[str]) -> int:
    blob = make_excel([{"product_name": n} for n in names])
    batch = parse_excel(db, file_bytes=blob, filename=filename, user_id=user_id)
    return batch.id


# ─── list_my_uploads ─────────────────────────────────────────


def test_list_my_uploads_isolates_by_current_user(db):
    """User 1 uploads; user 2 uploads. Each only sees their own.
    Cross-user leakage here = privacy bug, since batch contents may
    include supplier pricing the other user shouldn't see."""
    _seed_batch(db, user_id=1, filename="alice.xlsx", names=["A1"])
    _seed_batch(db, user_id=2, filename="bob.xlsx", names=["B1"])

    out_alice = REGISTRY.view(["list_my_uploads"]).dispatch(
        "list_my_uploads", {}, ctx=_ctx_with_deps(db, user_id=1)
    )
    out_bob = REGISTRY.view(["list_my_uploads"]).dispatch(
        "list_my_uploads", {}, ctx=_ctx_with_deps(db, user_id=2)
    )
    files_alice = {b["filename"] for b in json.loads(out_alice)["items"]}
    files_bob = {b["filename"] for b in json.loads(out_bob)["items"]}
    assert files_alice == {"alice.xlsx"}
    assert files_bob == {"bob.xlsx"}


def test_list_my_uploads_filters_by_status(db):
    """status="ready" → only `ready` batches (the freshly-parsed ones).
    The agent uses this to find a batch ready to commit without showing
    completed/rolled_back history."""
    bid1 = _seed_batch(db, user_id=1, filename="just-parsed.xlsx", names=["X"])
    # Hand-flip one batch to "completed" to verify the filter excludes it.
    from domains.masterdata.upload.models import UploadBatch

    completed = db.get(UploadBatch, bid1)
    # Make a second batch and mark it completed.
    bid2 = _seed_batch(db, user_id=1, filename="already-done.xlsx", names=["Y"])
    completed2 = db.get(UploadBatch, bid2)
    completed2.status = "completed"
    db.commit()

    out = REGISTRY.view(["list_my_uploads"]).dispatch(
        "list_my_uploads", {"status": "ready"}, ctx=_ctx_with_deps(db, user_id=1)
    )
    payload = json.loads(out)
    files = {b["filename"] for b in payload["items"]}
    assert "just-parsed.xlsx" in files
    assert "already-done.xlsx" not in files


def test_list_my_uploads_has_errors_filter(db):
    """`has_errors=True` returns batches with row-level errors regardless
    of status. This is the right param for user phrasings like "失败的
    批次" / "出错的上传" — `status="failed"` means the batch CRASHED,
    which is rare and almost never what the user means."""
    from domains.masterdata.upload.models import UploadBatch

    bid_clean = _seed_batch(db, user_id=1, filename="clean.xlsx", names=["X"])
    bid_dirty = _seed_batch(db, user_id=1, filename="dirty.xlsx", names=["Y"])
    # Mark `dirty` as resolved with row errors (the realistic shape:
    # the batch processed fine but some rows failed validation).
    dirty = db.get(UploadBatch, bid_dirty)
    dirty.status = "resolved"
    dirty.error_rows = 3
    db.commit()

    out = REGISTRY.view(["list_my_uploads"]).dispatch(
        "list_my_uploads",
        {"has_errors": True},
        ctx=_ctx_with_deps(db, user_id=1),
    )
    payload = json.loads(out)
    files = {b["filename"] for b in payload["items"]}
    assert "dirty.xlsx" in files
    assert "clean.xlsx" not in files


def test_list_my_uploads_status_failed_returns_hint_when_empty(db):
    """Self-correcting feedback: when the agent uses `status="failed"`
    but the user meant row-level errors, the tool result includes a
    `hint` counter pointing at `has_errors=true`. Without this, the
    agent gives up after one empty result (observed in L4 baseline)."""
    from domains.masterdata.upload.models import UploadBatch

    bid = _seed_batch(db, user_id=1, filename="resolved-with-errors.xlsx", names=["Z"])
    b = db.get(UploadBatch, bid)
    b.status = "resolved"
    b.error_rows = 2
    db.commit()

    out = REGISTRY.view(["list_my_uploads"]).dispatch(
        "list_my_uploads",
        {"status": "failed"},
        ctx=_ctx_with_deps(db, user_id=1),
    )
    payload = json.loads(out)
    assert payload["total_matching"] == 0
    assert "hint" in payload, "tool must self-correct when filter is wrong"
    assert payload["hint"]["batches_with_row_errors"] == 1
    assert "has_errors" in payload["hint"]["note"].lower()


# ─── parse_uploaded_file ─────────────────────────────────────


def test_parse_uploaded_file_returns_match_summary(db):
    """Tool returns JSON with batch_id, status, total_rows, matched counts.
    This is what the agent reads back to decide what to tell the user."""
    bid = _seed_batch(db, user_id=1, filename="x.xlsx", names=["NewItemA", "NewItemB"])

    out = REGISTRY.view(["parse_uploaded_file"]).dispatch(
        "parse_uploaded_file", {"batch_id": bid}, ctx=_ctx_with_deps(db, user_id=1)
    )
    # Successful call returns JSON — not an "Error:" string.
    assert not out.startswith("Error:")
    payload = json.loads(out)
    assert payload["batch_id"] == bid
    assert payload["status"] == "resolved"
    assert payload["total_rows"] == 2
    # Both rows were new (no product in DB) → new_rows == 2.
    assert payload["new_rows"] == 2


def test_parse_uploaded_file_cross_user_returns_error_string(db):
    """User 2 tries to parse user 1's batch → service raises
    BatchOwnedByOther → tool wraps it as "Error: ..." (not raised).
    Agents are trained to recognise the "Error:" prefix; raising would
    propagate as a tool-runner crash instead of a recoverable signal."""
    bid = _seed_batch(db, user_id=1, filename="theirs.xlsx", names=["X"])

    out = REGISTRY.view(["parse_uploaded_file"]).dispatch(
        "parse_uploaded_file", {"batch_id": bid}, ctx=_ctx_with_deps(db, user_id=2)
    )
    assert out.startswith("Error:")
    # Don't leak the batch contents in the message.
    assert "X" not in out  # product name should not be in error


def test_parse_uploaded_file_unknown_batch_id_returns_error(db):
    """Asking for a batch that doesn't exist → "Error:" prefix, never 500."""
    out = REGISTRY.view(["parse_uploaded_file"]).dispatch(
        "parse_uploaded_file", {"batch_id": 99999}, ctx=_ctx_with_deps(db, user_id=1)
    )
    assert out.startswith("Error:")


# ─── preview_upload ──────────────────────────────────────────


def test_preview_upload_returns_grouped_diff(db):
    """After resolve, preview returns {create, update, skip, error} groups.
    Each group is a list capped at `limit`."""
    bid = _seed_batch(db, user_id=1, filename="prev.xlsx", names=["P1", "P2"])
    resolve_and_score(db, batch_id=bid, user_id=1)

    out = REGISTRY.view(["preview_upload"]).dispatch(
        "preview_upload", {"batch_id": bid}, ctx=_ctx_with_deps(db, user_id=1)
    )
    assert not out.startswith("Error:")
    payload = json.loads(out)
    # Schema sanity: the four groups are present.
    for key in ("create", "update", "skip", "error"):
        assert key in payload, f"missing diff group: {key}"
    # Both rows are new products → both land in `create`.
    assert len(payload["create"]) == 2


def test_preview_upload_cross_user_returns_error(db):
    """Bob can't peek at Alice's diff. Cross-user → "Error:" string."""
    bid = _seed_batch(db, user_id=1, filename="alice-prev.xlsx", names=["A1"])
    resolve_and_score(db, batch_id=bid, user_id=1)

    out = REGISTRY.view(["preview_upload"]).dispatch(
        "preview_upload", {"batch_id": bid}, ctx=_ctx_with_deps(db, user_id=2)
    )
    assert out.startswith("Error:")
