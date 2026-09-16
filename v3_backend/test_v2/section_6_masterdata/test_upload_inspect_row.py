"""Section 6 — Masterdata upload pipeline, inspect_row drill-down.

测试目标：
    `_field_diff` 在 2026-05-19 改成了 4-state schema（change / unchanged /
    keep_db / set_new），并新增了 `inspect_row` 服务函数 + `inspect_upload_row`
    agent 工具用来回答 "第 N 行具体怎么样" 类问题。

    这一类问题的 prod 事故触发因素就是 agent 凭记忆瞎答 ——
    旧 `_field_diff` 只 emit changed 字段，agent 把"没在 diff 里"
    解读成"Excel 没填"，对用户胡说 "您 Excel 里没填 pack_size"。

    真实情况是 Excel 写了同一个值，命中 `unchanged` 分支。

    这个文件钉的契约：
      1. `_field_diff` 必须返回 14 个 mutable 字段每一个的 4-state 状态
      2. 4 种 action 都能被一行数据稳定触发
      3. `inspect_row` 同时返回 fields + identity + unrecognized_columns
      4. `inspect_upload_row` 工具对跨用户、不存在的行返回 "Error:" 字符串
      5. SKILL.md 仍包含 Step 5c HARD RULE 关键 token —— 防止以后改文档时
         偷偷把硬约束改没了

设计方法：
    一行 staging 数据，DB 端预先准备好 → 让 4 种 action 各命中一次 → 一并
    断言 service + tool + SKILL.md。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Side-effect: register v3 tools
from agent.runtime import tools as _v3_tools  # noqa: F401
from agent.runtime.deps import V3Deps, inject_deps
from domains.masterdata.upload import (
    inspect_row,
    parse_excel,
    preview_changes,
    resolve_and_score,
)
from domains.masterdata.upload.errors import UploadError
from general_agent import REGISTRY, ToolContext
from test_v2.fixtures.helpers import make_excel, seed_product


def _ctx_with_deps(db, *, user_id: int = 1) -> ToolContext:
    from domains.identity.models import User
    from test_v2.fixtures.helpers import seed_user

    if db.get(User, user_id) is None:
        user = seed_user(db, email=f"artifact-{user_id}@example.com")
        user.id = user_id
        db.commit()
    ctx = ToolContext(workspace=Path("/tmp"), extras={})
    inject_deps(ctx, V3Deps(db=db, user_id=user_id, user_role="employee"))
    return ctx


def _resolved(db, rows: list[dict], user_id: int = 1):
    blob = make_excel(rows)
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=user_id)
    return resolve_and_score(db, batch_id=batch.id, user_id=user_id)


# ─── 4-state schema completeness ─────────────────────────────


def test_field_diff_covers_all_19_mutable_fields(db):
    """`_field_diff` must return every field `_apply_update` is allowed to
    mutate. Used by inspect_row + preview_changes. Drift here
    means the agent will silently omit a field from drill-down."""
    seed_product(db, code="A1", name="Apple", price=10.0, unit="kg")
    batch = _resolved(
        db,
        [{"product_code": "A1", "product_name": "Apple", "price": 12.0}],
    )
    out = preview_changes(db, batch_id=batch.id, user_id=1)
    fields = out["update"][0]["fields"]

    expected = {
        "price", "contract_price", "unit", "pack_size",
        "product_name_jp", "brand", "unit_size",
        "country_of_origin", "currency",
        "category_id", "supplier_id", "country_id", "port_id",
        "effective_from", "effective_to",
        "purchase_price_effective_from", "purchase_price_effective_to",
        "selling_price_effective_from", "selling_price_effective_to",
    }
    assert set(fields.keys()) == expected, (
        f"missing fields: {expected - set(fields.keys())}, "
        f"extra fields: {set(fields.keys()) - expected}"
    )


def test_field_diff_action_change_when_excel_overwrites_db(db):
    """DB has price=10, Excel writes price=12 → action=change, will_write=True."""
    seed_product(db, code="A1", name="Apple", price=10.0)
    batch = _resolved(
        db,
        [{"product_code": "A1", "product_name": "Apple", "price": 12.0}],
    )
    out = preview_changes(db, batch_id=batch.id, user_id=1)
    price = out["update"][0]["fields"]["price"]
    assert price["action"] == "change"
    assert price["db"] == 10.0
    assert price["excel"] == 12.0
    assert price["will_write"] is True


def test_field_diff_action_unchanged_when_db_and_excel_equal(db):
    """DB and Excel have same unit → action=unchanged, will_write=False.
    Skip group exists exactly because every mutable field is unchanged."""
    seed_product(db, code="A1", name="Apple", price=10.0, unit="kg")
    batch = _resolved(
        db,
        [{"product_code": "A1", "product_name": "Apple", "price": 10.0, "unit": "kg"}],
    )
    preview_changes(db, batch_id=batch.id, user_id=1)
    # Whole row falls into skip; inspect it directly via service.
    row = inspect_row(db, batch_id=batch.id, row_index=1, user_id=1)
    unit = row["fields"]["unit"]
    assert unit["action"] == "unchanged"
    assert unit["db"] == "kg"
    assert unit["excel"] == "kg"
    assert unit["will_write"] is False
    # Price likewise — both sides 10.0
    assert row["fields"]["price"]["action"] == "unchanged"


def test_field_diff_action_keep_db_when_excel_omits_field(db):
    """DB has unit=kg, Excel doesn't include the unit column at all →
    action=keep_db, will_write=False. This is the case agent used to
    misread as 'Excel 没填'."""
    seed_product(db, code="A1", name="Apple", price=10.0, unit="kg")
    batch = _resolved(
        db,
        [{"product_code": "A1", "product_name": "Apple", "price": 12.0}],  # no unit col
    )
    row = inspect_row(db, batch_id=batch.id, row_index=1, user_id=1)
    unit = row["fields"]["unit"]
    assert unit["action"] == "keep_db"
    assert unit["db"] == "kg"
    assert unit["excel"] is None
    assert unit["will_write"] is False


def test_field_diff_action_set_new_when_db_empty_and_excel_provides(db):
    """DB has no brand, Excel writes brand → action=set_new, will_write=True."""
    seed_product(db, code="A1", name="Apple", price=10.0)  # default brand=None
    batch = _resolved(
        db,
        [{"product_code": "A1", "product_name": "Apple", "brand": "ACME"}],
    )
    row = inspect_row(db, batch_id=batch.id, row_index=1, user_id=1)
    brand = row["fields"]["brand"]
    assert brand["action"] == "set_new"
    assert brand["db"] is None
    assert brand["excel"] == "ACME"
    assert brand["will_write"] is True


# ─── inspect_row service contract ────────────────────────────


def test_inspect_row_returns_fields_identity_and_unrecognized_columns(db):
    """The full inspect_row payload has all three sections so the agent
    can answer "what's the row look like" without composing from multiple
    tools."""
    seed_product(db, code="A1", name="Apple", price=10.0)
    batch = _resolved(
        db,
        [{
            "product_code": "A1",
            "product_name": "Apple",
            "price": 12.0,
            # `mystery_column` is not in _HEADER_ALIASES — must surface
            # under unrecognized_columns rather than silently drop.
            "mystery_column": "garbage",
        }],
    )
    row = inspect_row(db, batch_id=batch.id, row_index=1, user_id=1)

    assert row["batch_id"] == batch.id
    assert row["row_index"] == 1
    assert row["match_status"] == "exact"
    assert "fields" in row
    assert "identity" in row
    # Identity always marked immutable
    assert row["identity"]["code"]["action"] == "immutable"
    assert row["identity"]["product_name_en"]["action"] == "immutable"
    assert row["identity"]["code"]["will_write"] is False
    # Unrecognised column surfaced
    assert "mystery_column" in row["unrecognized_columns"]


def test_inspect_row_for_new_product_uses_set_new_or_keep_db(db):
    """A row with no DB match (will INSERT) → identity is set_new, every
    field is either set_new (Excel had it) or keep_db (Excel didn't)."""
    batch = _resolved(
        db,
        [{"product_name": "Brand New", "price": 5.0}],
    )
    row = inspect_row(db, batch_id=batch.id, row_index=1, user_id=1)
    assert row["match_status"] == "new"
    assert row["identity"]["product_name_en"]["action"] == "set_new"
    assert row["fields"]["price"]["action"] == "set_new"
    assert row["fields"]["price"]["excel"] == 5.0
    # brand wasn't provided → keep_db (no DB side either, but action is keep_db)
    assert row["fields"]["brand"]["action"] == "keep_db"
    assert row["fields"]["brand"]["will_write"] is False


def test_inspect_row_missing_row_raises_upload_error(db):
    """row_index=999 doesn't exist → UploadError; this is the path that
    causes the tool wrapper to return 'Error:' to the agent."""
    batch = _resolved(db, [{"product_name": "Apple"}])
    with pytest.raises(UploadError):
        inspect_row(db, batch_id=batch.id, row_index=999, user_id=1)


# ─── inspect_upload_row tool wrapper ─────────────────────────


def test_inspect_upload_row_tool_returns_json(db):
    """Happy path — tool returns JSON-parseable string (not "Error:")."""
    seed_product(db, code="A1", name="Apple", price=10.0)
    batch = _resolved(
        db,
        [{"product_code": "A1", "product_name": "Apple", "price": 12.0}],
    )
    out = REGISTRY.view(["inspect_upload_row"]).dispatch(
        "inspect_upload_row",
        {"batch_id": batch.id, "row_index": 1},
        ctx=_ctx_with_deps(db, user_id=1),
    )
    assert not out.startswith("Error:")
    payload = json.loads(out)
    assert payload["row_index"] == 1
    assert payload["fields"]["price"]["action"] == "change"
    assert payload["fields"]["price"]["excel"] == 12.0


def test_inspect_upload_row_tool_cross_user_returns_error(db):
    """User 2 inspecting user 1's batch → 'Error:' (BatchOwnedByOther wrap)."""
    seed_product(db, code="A1", name="Apple", price=10.0)
    batch = _resolved(
        db,
        [{"product_code": "A1", "product_name": "Apple", "price": 12.0}],
        user_id=1,
    )
    out = REGISTRY.view(["inspect_upload_row"]).dispatch(
        "inspect_upload_row",
        {"batch_id": batch.id, "row_index": 1},
        ctx=_ctx_with_deps(db, user_id=2),
    )
    assert out.startswith("Error:")


def test_inspect_upload_row_tool_missing_row_returns_error(db):
    """row_index that doesn't exist → 'Error:' so the agent knows to
    apologise rather than fabricate values."""
    batch = _resolved(db, [{"product_name": "Apple"}])
    out = REGISTRY.view(["inspect_upload_row"]).dispatch(
        "inspect_upload_row",
        {"batch_id": batch.id, "row_index": 999},
        ctx=_ctx_with_deps(db, user_id=1),
    )
    assert out.startswith("Error:")


# ─── SKILL.md HARD RULE content-shape guard ──────────────────


def test_skill_md_step_5c_hard_rule_is_present():
    """The Step 5c HARD RULE prevents agent guessing about row-specific
    state. If this file gets edited and the rule disappears, that's a
    silent regression — we'd be back to the 2026-05-19 incident shape.

    This test asserts the load-bearing strings are still there. It does
    NOT assert wording verbatim — just that the contract pieces
    (forbid-guess, must-call-inspect, batch_id/row_index call shape) are
    each represented somewhere in the file.
    """
    skill_path = (
        Path(__file__).resolve().parents[2]
        / "skills" / "master-data-upload" / "SKILL.md"
    )
    assert skill_path.exists(), f"SKILL.md missing at {skill_path}"
    text = skill_path.read_text(encoding="utf-8")

    # Step 5c heading present
    assert "Step 5c" in text, "Step 5c heading missing from SKILL.md"

    # Forbids guessing / memory-based answers
    assert "绝对禁止" in text, (
        "Step 5c must forbid guessing — 绝对禁止 marker missing"
    )

    # Names the tool the agent MUST call instead
    assert "inspect_upload_row" in text, (
        "Step 5c must direct the agent to call inspect_upload_row"
    )

    # Tool's argument shape — batch_id + row_index — both named so the
    # agent can pattern-match the call from this SKILL alone.
    assert "batch_id" in text and "row_index" in text, (
        "Step 5c must mention batch_id + row_index call args"
    )

    # 4-state action keywords present so the agent learns the schema
    for keyword in ("change", "unchanged", "keep_db", "set_new"):
        assert keyword in text, f"SKILL.md must mention 4-state action '{keyword}'"
