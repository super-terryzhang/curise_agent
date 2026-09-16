"""Section 6 — Masterdata upload pipeline, Stage 2: `resolve_and_score`.

测试目标：
    每行 staging 与 `products` 表比对，按下述优先级打 confidence 分：
      - product_code 精确命中 → match_status="exact",      confidence=1.0
      - product_name 精确命中  → match_status="name_exact", confidence=0.95
      - product_name 模糊命中 (>=0.6) → match_status="fuzzy",  confidence=similarity
      - 阈值之下 → match_status="new"     (待创建)
      - 缺 product_name → match_status="error" + validation_errors

为什么重要：
    confidence 直接驱动 preview 分组（create / update / skip）。打错了一个
    confidence，整个 commit 阶段就会向错的产品里塞值。Stage 2 是 LLM-free 的
    纯字符串匹配，没理由出现"差不多匹配上了"的灰区——必须由测试锁死。

设计方法：
    用 helpers.seed_product 在 DB 里建好基准产品 → make_excel + parse_excel
    生成 staging → resolve_and_score → 取 StagingProduct 行断言 match_status 和 confidence。
"""

from __future__ import annotations

import pytest

from domains.masterdata.upload import parse_excel, resolve_and_score
from domains.masterdata.upload.errors import BatchOwnedByOther
from domains.masterdata.upload.models import StagingProduct
from test_v2.fixtures.helpers import make_excel, seed_product


def _stage_one_row(db, row: dict, user_id: int = 1):
    """Parse + resolve a single-row file and return its StagingProduct."""
    blob = make_excel([row])
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=user_id)
    resolve_and_score(db, batch_id=batch.id, user_id=user_id)
    return (
        batch,
        db.query(StagingProduct).filter(StagingProduct.batch_id == batch.id).one(),
    )


# ─── Exact code match (highest confidence) ──────────────────


def test_resolve_exact_code_match_scores_one_point_zero(db):
    """Same product_code in DB → confidence 1.0 even if the name disagrees."""
    target = seed_product(db, code="SKU-001", name="Original DB Name", price=10.0)
    batch, sp = _stage_one_row(
        db, {"product_code": "SKU-001", "product_name": "Different Name", "price": 12.0}
    )
    assert sp.match_status == "exact"
    assert sp.confidence == 1.0
    assert sp.match_target_id == target.id
    assert batch.matched_exact == 1


def test_resolve_code_match_is_case_insensitive(db):
    """`SKU-001` in DB, `sku-001` in upload → still exact match."""
    target = seed_product(db, code="SKU-001", name="X")
    _, sp = _stage_one_row(db, {"product_code": "sku-001", "product_name": "x"})
    assert sp.match_status == "exact"
    assert sp.match_target_id == target.id


# ─── Name-exact when code missing ──────────────────────────


def test_resolve_name_exact_when_code_absent_scores_zero_point_nine_five(db):
    """No product_code on the staging row + exact name hit → 0.95."""
    target = seed_product(db, code=None, name="Banana Republic")
    _, sp = _stage_one_row(db, {"product_name": "Banana Republic"})
    assert sp.match_status == "name_exact"
    assert sp.confidence == 0.95
    assert sp.match_target_id == target.id


def test_resolve_name_match_is_case_insensitive(db):
    target = seed_product(db, code=None, name="Banana Republic")
    _, sp = _stage_one_row(db, {"product_name": "banana republic"})
    assert sp.match_status == "name_exact"
    assert sp.match_target_id == target.id


# ─── Fuzzy match removal (2026-05-25) ─────────────────────


def test_resolve_similar_but_distinct_name_goes_to_new(db):
    """Fuzzy similarity matching was removed 2026-05-25 — prod incident:
    "lettuce mizuna" matched "lettuce mesclun mix" at 0.667 similarity,
    causing the customer's brand-new mizuna SKU to be silently treated
    as an update to an unrelated lettuce. Industry standard (SAP / Coupa
    / NetSuite) requires explicit code or name match; ambiguity → `new`."""
    seed_product(db, code="X1", name="LETTUCE MESCLUN MIX")
    _, sp = _stage_one_row(db, {"product_name": "LETTUCE MIZUNA"})  # ~0.667 SequenceMatcher
    assert sp.match_status == "new", (
        "fuzzy-similar but distinct names must NOT auto-match — that was "
        "the 2026-05-25 bug. Force the user to confirm via 'new'."
    )
    assert sp.match_target_id is None
    assert sp.confidence is None


def test_resolve_typo_no_longer_auto_corrects(db):
    """Even a one-letter typo against an existing product is `new` now.
    Trade-off: customer must clean their data; system never silently
    'corrects' to a different SKU."""
    seed_product(db, code="X1", name="Cherry Tomato Premium")
    _, sp = _stage_one_row(db, {"product_name": "Cherry Tomatos Premium"})  # typo
    assert sp.match_status == "new"
    assert sp.match_target_id is None


def test_resolve_below_threshold_marks_row_new(db):
    """No remotely-similar product in DB → status="new" (creation candidate)."""
    seed_product(db, code="X1", name="Cherry Tomato")
    _, sp = _stage_one_row(db, {"product_name": "Yacht Sailing Equipment Set"})
    assert sp.match_status == "new"
    assert sp.match_target_id is None
    assert sp.confidence is None


# ─── Error rows ────────────────────────────────────────────


def test_resolve_row_with_missing_product_name_flagged_error(db):
    """Row reached staging with blank product_name → marked error, not crash.

    NB: Stage 1 only stages a row if some column maps to product_name in the
    HEADER. A row with that column present but value-blank still goes to staging,
    and Stage 2 must surface this as a validation error instead of trying to
    fuzzy-match an empty string."""
    blob = make_excel(
        [
            {"product_name": "Valid", "price": 1.0},
            {"product_name": "", "price": 2.0},
        ]
    )
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=1)
    resolve_and_score(db, batch_id=batch.id, user_id=1)

    rows = (
        db.query(StagingProduct)
        .filter(StagingProduct.batch_id == batch.id)
        .order_by(StagingProduct.row_index)
        .all()
    )
    assert rows[0].match_status in ("new", "exact", "name_exact")
    assert rows[1].match_status == "error"
    assert rows[1].validation_errors
    assert any("product_name" in msg for msg in rows[1].validation_errors)

    db.refresh(batch)
    assert batch.error_rows == 1


# ─── Batch counters ────────────────────────────────────────


def test_resolve_updates_batch_counters_for_mixed_rows(db):
    """A 4-row batch with three outcomes (exact/name_exact/new) → counters add to 4.

    Updated 2026-05-25: fuzzy bucket was removed; the previously-fuzzy row
    (one-letter typo against existing name) now falls into `new`."""
    seed_product(db, code="A1", name="Apple")
    seed_product(db, code=None, name="Cherry Tomato")

    blob = make_excel(
        [
            {"product_code": "A1", "product_name": "Apple"},                # exact
            {"product_name": "Cherry Tomato"},                              # name_exact
            {"product_name": "Cherry Tomatos"},                             # was fuzzy → new
            {"product_name": "Yacht Sailing Equipment Set"},                # new
        ]
    )
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=1)
    resolve_and_score(db, batch_id=batch.id, user_id=1)
    db.refresh(batch)

    # exact + name_exact share the same counter
    assert batch.matched_exact == 2
    assert batch.matched_fuzzy == 0  # no longer produced (kept for column compat)
    assert batch.new_rows == 2        # typo row + truly-new row both land here
    assert batch.error_rows == 0
    assert batch.status == "resolved"


# ─── Cross-user safety ─────────────────────────────────────


def test_resolve_cross_user_raises_batch_owned_by_other(db):
    """User 2 calling resolve on user 1's batch must hit BatchOwnedByOther."""
    seed_product(db, code="A1", name="Apple")
    blob = make_excel([{"product_code": "A1", "product_name": "Apple"}])
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=1)
    with pytest.raises(BatchOwnedByOther):
        resolve_and_score(db, batch_id=batch.id, user_id=2)
