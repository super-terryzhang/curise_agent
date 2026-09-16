"""Section 6 — Masterdata upload pipeline, Stage 5: `rollback_batch`.

测试目标：
    根据 ChangeLog 把一个已 commit 的批次反向回去：
      - 每条 `action="create"` → 删除该 product 行
      - 每条 `action="update"` → 把对应字段恢复成 old_value
      - 已经 rolled_back 状态再调一次 → 幂等（返回全 0 counters，不报错也不重复执行）
      - 只有原始上传者本人能 rollback（user_id 不匹配 → BatchOwnedByOther）

为什么重要：
    Stage 5 是给用户的"撤销键"。回滚错了 = 数据损坏（删错产品、用错的旧值覆盖回去）。
    幂等性失败 = 用户点两次撤销，第二次把已经恢复的字段二度改写。
    跨用户检查失败 = 任何人都能撤销别人的上传。

设计方法：
    跑完整 4 阶段先 commit，确认 Product 行确实有变 → 调 rollback_batch →
    用直接 SQL 验证 Product 行被删 / 字段被还原 / batch.status="rolled_back"。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from domains.masterdata.models import Product
from domains.masterdata.upload import (
    commit_batch,
    parse_excel,
    resolve_and_score,
    rollback_batch,
)
from domains.masterdata.upload.errors import BatchOwnedByOther
from test_v2.fixtures.helpers import make_excel, seed_product


def _commit(db, rows: list[dict], user_id: int = 1):
    blob = make_excel(rows)
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=user_id)
    resolve_and_score(db, batch_id=batch.id, user_id=user_id)
    commit_batch(db, batch_id=batch.id, user_id=user_id)
    db.refresh(batch)
    return batch


# ─── Delete created rows ────────────────────────────────────


def test_rollback_deletes_products_created_by_the_batch(db):
    """`action="create"` ChangeLog rows → the corresponding Product is deleted."""
    batch = _commit(
        db,
        [
            {"product_name": "Rollback Created A"},
            {"product_name": "Rollback Created B"},
        ],
    )
    # Pre-rollback baseline: products exist
    assert (
        db.query(Product)
        .filter(Product.product_name_en.like("Rollback Created %"))
        .count()
        == 2
    )

    result = rollback_batch(db, batch_id=batch.id, user_id=1)
    assert result["deleted"] == 2

    db.refresh(batch)
    assert batch.status == "rolled_back"
    assert (
        db.query(Product)
        .filter(Product.product_name_en.like("Rollback Created %"))
        .count()
        == 0
    )


# ─── Restore updated fields ─────────────────────────────────


def test_rollback_restores_updated_fields_to_pre_commit_values(db):
    """For each `action="update"` ChangeLog row, the field is set back to old_value."""
    p = seed_product(db, code="A1", name="Apple", price=10.0, unit="kg")
    original_id = p.id
    batch = _commit(
        db,
        [{"product_code": "A1", "product_name": "Apple", "price": 12.0, "unit": "lb"}],
    )

    # Sanity: the commit actually changed the row
    p_committed = db.get(Product, original_id)
    assert float(p_committed.price) == 12.0
    assert p_committed.unit == "lb"

    result = rollback_batch(db, batch_id=batch.id, user_id=1)
    assert result["restored"] >= 2  # one per field

    p_after = db.get(Product, original_id)
    assert float(p_after.price) == 10.0
    assert p_after.unit == "kg"


def test_rollback_restores_price_to_zero_when_old_value_was_zero(db):
    """Decimal(0) round-trip: if old_value="0", rollback writes Decimal('0') back."""
    seed_product(db, code="Z1", name="Was Zero", price=0.0)
    batch = _commit(
        db, [{"product_code": "Z1", "product_name": "Was Zero", "price": 5.5}]
    )
    rollback_batch(db, batch_id=batch.id, user_id=1)

    p = db.query(Product).filter(Product.code == "Z1").one()
    # NB: old_value was stringified from float(target.price)=0.0 → "0.0".
    # Production code rebuilds via Decimal("0.0") which equals Decimal("0").
    assert p.price == Decimal("0")


# ─── Mixed create + update ─────────────────────────────────


def test_rollback_handles_create_and_update_in_one_batch(db):
    """Mixed batch (1 create + 1 update) → deleted=1, restored>=1."""
    seed_product(db, code="A1", name="Apple", price=10.0)
    batch = _commit(
        db,
        [
            {"product_code": "A1", "product_name": "Apple", "price": 22.0},
            {"product_code": "", "product_name": "Mixed New", "price": None},
        ],
    )

    result = rollback_batch(db, batch_id=batch.id, user_id=1)
    assert result["deleted"] == 1
    assert result["restored"] >= 1

    # Apple's price restored
    apple = db.query(Product).filter(Product.code == "A1").one()
    assert float(apple.price) == 10.0
    # Mixed New is gone
    assert (
        db.query(Product).filter(Product.product_name_en == "Mixed New").count() == 0
    )


# ─── Idempotency ───────────────────────────────────────────


def test_rollback_is_idempotent_on_already_rolled_back_batch(db):
    """A second rollback returns all-zero counters and doesn't crash or re-delete."""
    batch = _commit(db, [{"product_name": "Idempotent X"}])
    first = rollback_batch(db, batch_id=batch.id, user_id=1)
    assert first["deleted"] == 1

    second = rollback_batch(db, batch_id=batch.id, user_id=1)
    assert second == {"deleted": 0, "restored": 0, "skipped": 0}

    db.refresh(batch)
    assert batch.status == "rolled_back"


# ─── Cross-user safety ─────────────────────────────────────


def test_rollback_cross_user_raises_batch_owned_by_other(db):
    """User 2 trying to undo user 1's batch → BatchOwnedByOther; the Product stays."""
    batch = _commit(db, [{"product_name": "Protected From Bob"}], user_id=1)
    assert (
        db.query(Product).filter(Product.product_name_en == "Protected From Bob").count()
        == 1
    )

    with pytest.raises(BatchOwnedByOther):
        rollback_batch(db, batch_id=batch.id, user_id=2)

    # Confirm: no half-rollback occurred
    assert (
        db.query(Product).filter(Product.product_name_en == "Protected From Bob").count()
        == 1
    )
    db.refresh(batch)
    assert batch.status == "completed"  # still committed, not flipped
