"""Section 6 — Masterdata upload pipeline, Stage 4: `commit_batch`.

测试目标：
    把 resolve 完的 staging 行真写进 `products` 表：
      - `new` 行    → 新建 Product，并写一条 action="create" 的 ChangeLog
      - `exact/name_exact/fuzzy` 行 → 更新 Product 的字段，每改一个字段写一条
                                     action="update" 的 ChangeLog
      - 每行用 savepoint 包裹，一条出错不影响整批
      - 提交完毕 → batch.status="completed", committed_at=now
      - ChangeLog.user_id 必须落到当前调用者，回滚审计才能追责
      - **Decimal(0) 必须保留为 0，不能被 `if x.price` 误判成 None**
        （这是 v2 历史 bug，CLAUDE.md 数据上传系统章节有记录）

为什么重要：
    Commit 是流水线里唯一会写 `products` 的阶段，写错了没法回退到没上传过的状态
    （只能靠 changelog 还原）。所以每个字段的 old/new、user_id、状态机迁移必须按
    契约来。

设计方法：
    helpers.seed_product 准备底子产品 → make_excel + parse + resolve →
    commit_batch → 直接 SQL 查 Product / ChangeLog 断言。
"""

from __future__ import annotations

from decimal import Decimal

from domains.masterdata.models import Product
from domains.masterdata.upload import (
    commit_batch,
    parse_excel,
    resolve_and_score,
)
from domains.masterdata.upload.models import ProductChangeLog, UploadBatch
from test_v2.fixtures.helpers import make_excel, seed_product


def _resolved(db, rows: list[dict], user_id: int = 1):
    blob = make_excel(rows)
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=user_id)
    return resolve_and_score(db, batch_id=batch.id, user_id=user_id)


# ─── create rows ────────────────────────────────────────────


def test_commit_creates_new_products_from_new_rows(db):
    """`new` rows turn into actual Product rows in DB."""
    batch = _resolved(
        db,
        [
            {"product_name": "Brand New Alpha", "price": 10.0, "unit": "kg"},
            {"product_name": "Brand New Beta", "price": 20.0, "unit": "ea"},
        ],
    )
    result = commit_batch(db, batch_id=batch.id, user_id=1)

    assert result["created"] == 2
    assert result["updated"] == 0

    products = (
        db.query(Product)
        .filter(Product.product_name_en.in_(["Brand New Alpha", "Brand New Beta"]))
        .order_by(Product.product_name_en)
        .all()
    )
    assert len(products) == 2
    assert float(products[0].price) == 10.0
    assert products[0].unit == "kg"


def test_commit_writes_create_changelog_per_new_product(db):
    """Every new product → one `action="create"` ChangeLog row."""
    batch = _resolved(db, [{"product_name": "Brand New X"}, {"product_name": "Brand New Y"}])
    commit_batch(db, batch_id=batch.id, user_id=1)

    logs = (
        db.query(ProductChangeLog)
        .filter(
            ProductChangeLog.batch_id == batch.id,
            ProductChangeLog.action == "create",
        )
        .all()
    )
    assert len(logs) == 2
    # The new product's name lands in `new_value` so rollback can identify the row.
    new_values = {log.new_value for log in logs}
    assert new_values == {"Brand New X", "Brand New Y"}


# ─── update rows ────────────────────────────────────────────


def test_commit_updates_existing_products_for_matched_rows(db):
    """Resolved-as-exact rows → Product.price/unit/pack_size mutated."""
    seed_product(db, code="A1", name="Apple", price=10.0, unit="kg")
    batch = _resolved(
        db,
        [{"product_code": "A1", "product_name": "Apple", "price": 12.0, "unit": "lb"}],
    )
    result = commit_batch(db, batch_id=batch.id, user_id=1)
    assert result["updated"] == 1

    p = db.query(Product).filter(Product.code == "A1").one()
    assert float(p.price) == 12.0
    assert p.unit == "lb"


def test_commit_writes_one_changelog_row_per_changed_field(db):
    """Three fields changed (price, unit, pack_size) → three ChangeLog rows."""
    seed_product(db, code="A1", name="Apple", price=10.0, unit="kg")
    batch = _resolved(
        db,
        [
            {
                "product_code": "A1",
                "product_name": "Apple",
                "price": 12.0,
                "unit": "lb",
                "pack_size": "10x1KG",
            }
        ],
    )
    commit_batch(db, batch_id=batch.id, user_id=1)

    logs = (
        db.query(ProductChangeLog)
        .filter(
            ProductChangeLog.batch_id == batch.id,
            ProductChangeLog.action == "update",
        )
        .all()
    )
    fields_changed = {log.field_name for log in logs}
    assert fields_changed == {"price", "unit", "pack_size"}

    # Each log carries the field-level old/new diff.
    by_field = {log.field_name: log for log in logs}
    assert by_field["price"].old_value == "10.0"
    assert by_field["price"].new_value == "12.0"
    assert by_field["unit"].old_value == "kg"
    assert by_field["unit"].new_value == "lb"


# ─── user_id stamping ──────────────────────────────────────


def test_commit_stamps_user_id_on_every_changelog_row(db):
    """ChangeLog.user_id is the committer, used by the rollback audit trail."""
    batch = _resolved(db, [{"product_name": "X", "price": 5.0}], user_id=42)
    commit_batch(db, batch_id=batch.id, user_id=42)

    logs = db.query(ProductChangeLog).filter(ProductChangeLog.batch_id == batch.id).all()
    assert logs
    assert {log.user_id for log in logs} == {42}


# ─── batch lifecycle ───────────────────────────────────────


def test_commit_marks_batch_completed_and_sets_timestamp(db):
    """status transitions ready → resolved → completed; committed_at set."""
    batch = _resolved(db, [{"product_name": "Y", "price": 1.0}])
    assert batch.committed_at is None
    commit_batch(db, batch_id=batch.id, user_id=1)
    db.refresh(batch)
    assert batch.status == "completed"
    assert batch.committed_at is not None


def test_commit_persists_per_outcome_counts_on_batch(db):
    """`created_count` and `updated_count` on the UploadBatch row reflect the run.

    NB: every row in the make_excel list MUST share the same columns; the helper
    builds the header from the first row's keys only. So we keep product_code
    + price on every row even when blank.
    """
    seed_product(db, code="EXIST", name="Existing", price=10.0)
    batch = _resolved(
        db,
        [
            {"product_code": "", "product_name": "Brand New Q", "price": None},
            {"product_code": "", "product_name": "Brand New R", "price": None},
            {"product_code": "EXIST", "product_name": "Existing", "price": 22.0},
        ],
    )
    commit_batch(db, batch_id=batch.id, user_id=1)
    db.refresh(batch)
    assert batch.created_count == 2
    assert batch.updated_count == 1


# ─── Decimal(0) regression ─────────────────────────────────


def test_commit_preserves_zero_price_as_decimal_zero(db):
    """A row with `price=0` must persist Decimal('0'), not None.

    Regression test for the v2 bug (see CLAUDE.md "Data Upload System v2"):
    `if x.price else None` would treat Decimal('0') as falsy → silently
    dropping the price. The fix is `is not None` everywhere.
    """
    batch = _resolved(db, [{"product_name": "Free Sample", "price": 0}])
    commit_batch(db, batch_id=batch.id, user_id=1)

    p = db.query(Product).filter(Product.product_name_en == "Free Sample").one()
    assert p.price is not None
    assert p.price == Decimal("0")
    assert float(p.price) == 0.0


def test_commit_persists_existing_zero_price_unchanged(db):
    """Seed a product with price=0, upload with the same price → no false update."""
    seed_product(db, code="ZERO", name="Zero Priced", price=0.0)
    batch = _resolved(
        db, [{"product_code": "ZERO", "product_name": "Zero Priced", "price": 0}]
    )
    result = commit_batch(db, batch_id=batch.id, user_id=1)
    # price hasn't changed → not counted as updated
    assert result["updated"] == 0
    # And no spurious price changelog row exists
    price_logs = (
        db.query(ProductChangeLog)
        .filter(
            ProductChangeLog.batch_id == batch.id,
            ProductChangeLog.action == "update",
            ProductChangeLog.field_name == "price",
        )
        .all()
    )
    assert price_logs == []


# ─── Idempotency / counters ────────────────────────────────


def test_commit_count_matches_db_row_count(db):
    """`result['created']` matches the number of new Product rows actually inserted."""
    batch = _resolved(
        db,
        [
            {"product_name": "BatchCheck A"},
            {"product_name": "BatchCheck B"},
            {"product_name": "BatchCheck C"},
        ],
    )
    result = commit_batch(db, batch_id=batch.id, user_id=1)
    assert result["created"] == 3

    in_db = (
        db.query(Product)
        .filter(Product.product_name_en.like("BatchCheck %"))
        .count()
    )
    assert in_db == 3

    fresh = db.get(UploadBatch, batch.id)
    assert fresh.status == "completed"


# ─── Per-row savepoint isolation ────────────────────────────
#
# The commit loop wraps each row in `db.begin_nested()` so a single bad
# row can't roll back the whole batch. Pre-savepoint design: one DB
# constraint violation poisoned the session and the user got 0 rows
# inserted even though only 1 was malformed. Pin the isolation here.


def test_commit_per_row_failure_does_not_poison_other_rows(db, monkeypatch):
    """3 rows; we force `_apply_create` to raise on the middle one. Rows 1
    and 3 must still commit; the error is recorded against row 2 only."""
    from domains.masterdata.upload import service as upload_service

    batch = _resolved(
        db,
        [
            {"product_name": "Survivor One"},
            {"product_name": "Poison Pill"},
            {"product_name": "Survivor Three"},
        ],
    )

    original = upload_service._apply_create

    def flaky(db_, sp, batch_id, user_id):
        if sp.product_name == "Poison Pill":
            raise RuntimeError("simulated row failure")
        return original(db_, sp, batch_id, user_id)

    monkeypatch.setattr(upload_service, "_apply_create", flaky)

    result = upload_service.commit_batch(db, batch_id=batch.id, user_id=1)

    # The two survivors did commit; the bad row is counted as an error.
    assert result["created"] == 2
    assert result["errors"] >= 1
    survivors = (
        db.query(Product)
        .filter(Product.product_name_en.in_(["Survivor One", "Survivor Three"]))
        .count()
    )
    assert survivors == 2
    # The bad row never made it.
    poisoned = (
        db.query(Product)
        .filter(Product.product_name_en == "Poison Pill")
        .count()
    )
    assert poisoned == 0


# ─── State machine guard ─────────────────────────────────────


def test_commit_in_wrong_state_raises_batch_in_wrong_state(db):
    """Committing twice on the same batch must error out cleanly — the
    second call sees status=completed and refuses. Without this guard a
    user could accidentally double-commit (duplicate creates / phantom
    update churn in changelog)."""
    from domains.masterdata.upload.errors import BatchInWrongState

    batch = _resolved(db, [{"product_name": "Just Once"}])
    commit_batch(db, batch_id=batch.id, user_id=1)

    # Second call: batch is now in `completed`, not `resolved` → guard fires.
    import pytest

    with pytest.raises(BatchInWrongState) as exc:
        commit_batch(db, batch_id=batch.id, user_id=1)
    assert "resolved" in str(exc.value)
