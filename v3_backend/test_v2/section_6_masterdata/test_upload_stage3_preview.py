"""Section 6 — Masterdata upload pipeline, Stage 3: `preview_changes`.

测试目标：
    把 Stage 2 打过分的 staging 行组织成给前端看的 diff：
      - new            → create 组（要插入的新产品）
      - 字段有变化      → update 组（每行带 fields={field: {old, new}}）
      - 字段全等        → skip   组（reason="no field changes"）
      - validation_errors → error 组
    每组最多 50 行，超过则 `truncated=True`。
    返回的 `summary` counts 是从 batch 的计数器读的，不是 len(create/update/...)，
    所以 truncated 之后 summary 仍是真实总数。

为什么重要：
    这是给用户"按下确认前"看的最后一屏。如果 skip 误判成 update，会出现"没改东西
    也写一条 changelog"的脏数据；如果 truncated 标记忘了 set，前端就会以为只有 50
    条变更而其实是 500。

设计方法：
    用 helpers.seed_product 在 DB 里准备好底子产品 → 跑 parse + resolve →
    调 preview_changes → 断言 summary / 分组列表 / diff 字段。
"""

from __future__ import annotations

import pytest
from sqlalchemy import event

from domains.masterdata.upload import (
    parse_excel,
    preview_changes,
    resolve_and_score,
)
from domains.masterdata.upload.errors import BatchOwnedByOther
from test_v2.fixtures.helpers import make_excel, seed_product


def _resolved(db, rows: list[dict], user_id: int = 1):
    blob = make_excel(rows)
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=user_id)
    return resolve_and_score(db, batch_id=batch.id, user_id=user_id)


# ─── Group composition ──────────────────────────────────────


def test_preview_groups_create_and_update_separately(db):
    """One new row + one diffing row → one in create, one in update."""
    seed_product(db, code="A1", name="Apple", price=10.0)
    batch = _resolved(
        db,
        [
            {"product_code": "A1", "product_name": "Apple", "price": 12.0},  # update
            {"product_code": "Z9", "product_name": "Brand New Thing"},        # create
        ],
    )
    out = preview_changes(db, batch_id=batch.id, user_id=1)

    assert isinstance(out, dict)
    assert set(out.keys()) >= {"summary", "create", "update", "skip", "error", "truncated"}
    assert out["summary"]["create"] == 1
    assert out["summary"]["update"] == 1
    assert len(out["create"]) == 1
    assert len(out["update"]) == 1


def test_preview_update_diff_shows_4state_per_field(db):
    """As of 2026-05-19, the diff sub-object is the 4-state schema:
    `{field: {action, db, excel, will_write}}`. Fields the upload will
    actually mutate have action ∈ {change, set_new} and will_write=True.
    (Replaces the old changed-only `{old, new}` schema — see
    docs/current_progress/2026-05-19/ for the prod incident that drove
    the change: agent guessing about fields that were unchanged.)"""
    seed_product(db, code="A1", name="Apple", price=10.0, unit="kg")
    batch = _resolved(
        db,
        [{"product_code": "A1", "product_name": "Apple", "price": 12.5, "unit": "lb"}],
    )
    out = preview_changes(db, batch_id=batch.id, user_id=1)
    diff = out["update"][0]["fields"]

    # `change` action — Excel value differs from DB value
    assert diff["price"]["action"] == "change"
    assert diff["price"]["db"] == 10.0
    assert diff["price"]["excel"] == 12.5
    assert diff["price"]["will_write"] is True

    assert diff["unit"]["action"] == "change"
    assert diff["unit"]["db"] == "kg"
    assert diff["unit"]["excel"] == "lb"
    assert diff["unit"]["will_write"] is True

    # Convenience field listing only the fields the commit will write
    assert set(out["update"][0]["will_write_fields"]) == {"price", "unit"}


def test_preview_no_change_row_goes_to_skip_with_reason(db):
    """Staging row matches an existing product byte-for-byte → skip group."""
    seed_product(db, code="A1", name="Apple", price=10.0, unit="kg")
    batch = _resolved(
        db,
        [{"product_code": "A1", "product_name": "Apple", "price": 10.0, "unit": "kg"}],
    )
    out = preview_changes(db, batch_id=batch.id, user_id=1)
    assert out["update"] == []
    assert len(out["skip"]) == 1
    assert out["skip"][0]["reason"] == "no field changes"
    assert out["skip"][0]["product_id"]  # links back to the matched product


# ─── Truncation cap ─────────────────────────────────────────


def test_preview_caps_each_group_at_fifty_by_default(db):
    """60 new rows + limit=50 → exactly 50 in `create`, `truncated=True`,
    but `summary.create` still reports the real total 60."""
    rows = [{"product_name": f"Brand New Item {i:03d}"} for i in range(60)]
    batch = _resolved(db, rows)
    out = preview_changes(db, batch_id=batch.id, user_id=1, limit=50)

    assert len(out["create"]) == 50
    assert out["truncated"] is True
    assert out["summary"]["create"] == 60


def test_preview_truncated_false_when_under_limit(db):
    """Under the cap → truncated=False, all rows present."""
    rows = [{"product_name": f"Brand New {i}"} for i in range(5)]
    batch = _resolved(db, rows)
    out = preview_changes(db, batch_id=batch.id, user_id=1)
    assert len(out["create"]) == 5
    assert out["truncated"] is False


def test_preview_custom_limit_truncates_each_group_independently(db):
    """`limit=2` + 4 new rows + 4 update rows would truncate both groups."""
    for i in range(4):
        seed_product(db, code=f"U{i}", name=f"Existing {i}", price=10.0)
    rows = (
        [{"product_code": f"U{i}", "product_name": f"Existing {i}", "price": 20.0} for i in range(4)]
        + [{"product_name": f"Brand New {i}"} for i in range(4)]
    )
    batch = _resolved(db, rows)
    out = preview_changes(db, batch_id=batch.id, user_id=1, limit=2)
    assert len(out["create"]) == 2
    assert len(out["update"]) == 2
    assert out["truncated"] is True


def test_preview_query_count_does_not_scale_with_update_rows(db):
    """Step 3 must preload targets/FK maps instead of querying four masters per row."""
    for i in range(60):
        seed_product(db, code=f"B{i}", name=f"Bulk {i}", price=10.0)
    batch = _resolved(
        db,
        [
            {"product_code": f"B{i}", "product_name": f"Bulk {i}", "price": 20.0}
            for i in range(60)
        ],
    )
    statements = 0

    def count_statement(*_args):
        nonlocal statements
        statements += 1

    event.listen(db.get_bind(), "before_cursor_execute", count_statement)
    try:
        out = preview_changes(db, batch_id=batch.id, user_id=1, limit=25)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", count_statement)

    assert out["summary"]["update"] == 60
    assert statements < 15


# ─── Cross-user safety ─────────────────────────────────────


def test_preview_cross_user_raises_batch_owned_by_other(db):
    """User 2 calling preview on user 1's batch must hit BatchOwnedByOther."""
    seed_product(db, code="A1", name="Apple")
    batch = _resolved(db, [{"product_code": "A1", "product_name": "Apple"}], user_id=1)
    with pytest.raises(BatchOwnedByOther):
        preview_changes(db, batch_id=batch.id, user_id=2)
