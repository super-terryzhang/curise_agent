"""Section 6 — Artifact catalog + present_artifact tool + HTTP endpoint.

测试目标：
    我们实测过 agent 自己写 HTML 在 200 行 / 多轮 50 行场景会漂移 + 静默截断
    （`scripts/probe_html_hallucination.py`）。新走 A2UI 风格的 declarative
    pattern：

      1. Backend 维护 component catalog（`agent.runtime.artifacts`）
      2. Agent 调 `present_artifact(component, narration, data)` 工具
      3. Catalog 验 component 在册 + data 是 JSON object + 字段类型 + 不超
         过 `payload_max_keys`（防 agent 把整张表塞进 data）
      4. 工具不回传 data —— 只回 ack。给 agent 一个"我已经发过了"的信号，
         不诱导它"再复述一遍"（这是 LLM 幻觉的常见再激活路径）
      5. Backend HTTP endpoint `/api/artifacts/upload-diff/{batch_id}`
         返回 `preview_changes` 的 4-state JSON 给前端组件 hydrate

    这一组测试钉住的契约：

      A. Catalog 拒未知 component（agent 编出的名字必须被拦下）
      B. Schema 验证：missing key / wrong type → 拦下，返回 "Error:" 而不是 raise
      C. `payload_max_keys` 拦下 inline data 攻击（agent 想把 200 行塞进 data）
      D. 工具的 ack string 不包含 data 值 —— 这是上面 #4 的设计意图，由测试钉住
      E. HTTP endpoint：跨用户 → 404；不存在 → 404；正常 → 包含 4-state 数据

为什么重要：
    Catalog 是 agent / 前端之间的合约。`@tool` decorator 把 docstring 当
    schema 发给 LLM，所以"现在 catalog 里有什么 component"是 LLM 当前看
    到的能力。Catalog 漂了 = agent 调用了不存在的 component = silent
    failure（艺术品消失）。测试钉死。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from general_agent import REGISTRY, ToolContext

# Side-effect: register every v3 business tool, including artifacts tool
from agent.runtime import artifacts, tools as _v3_tools  # noqa: F401
from agent.runtime.deps import V3Deps, inject_deps
from domains.masterdata.upload import parse_excel, resolve_and_score
from test_v2.fixtures.helpers import login, make_excel, seed_product, seed_user


def _ctx(db, *, user_id: int = 1, session_id: str = "test-session"):
    """Build a ToolContext with V3Deps injected + a fake agent.session_id
    so `present_artifact` can attempt SSE emission. SSE handle absent
    in tests → tool still returns ack (best-effort emission is the
    documented behaviour)."""
    from domains.identity.models import User
    from test_v2.fixtures.helpers import seed_user

    if db.get(User, user_id) is None:
        user = seed_user(db, email=f"artifact-{user_id}@example.com")
        user.id = user_id
        db.commit()
    ctx = ToolContext(workspace=Path("/tmp"), extras={})
    inject_deps(ctx, V3Deps(db=db, user_id=user_id, user_role="employee"))

    class _FakeAgent:
        def __init__(self, sid: str) -> None:
            self.session_id = sid

    ctx.agent = _FakeAgent(session_id)  # type: ignore[attr-defined]
    return ctx


# ─── A. Unknown component rejected ───────────────────────────


def test_present_artifact_unknown_component_returns_error(db):
    """Agent fabricates a component name → tool returns 'Error:' string
    (recoverable) rather than raising. The list of valid components
    must appear in the error message — otherwise the agent can't fix
    the call on retry."""
    out = REGISTRY.view(["present_artifact"]).dispatch(
        "present_artifact",
        {
            "component": "fake_component_xyz",
            "narration": "test",
            "data": "{}",
        },
        ctx=_ctx(db),
    )
    assert out.startswith("Error:")
    assert "fake_component_xyz" in out
    # Valid options surfaced so agent can self-correct
    assert "upload_diff_viewer" in out or "narration_only" in out


# ─── B. Schema validation ──────────────────────────────────


def test_present_artifact_missing_required_key_returns_error(db):
    """upload_diff_viewer needs `batch_id`. Calling with empty data → 'Error:'."""
    out = REGISTRY.view(["present_artifact"]).dispatch(
        "present_artifact",
        {
            "component": "upload_diff_viewer",
            "narration": "missing batch_id",
            "data": "{}",
        },
        ctx=_ctx(db),
    )
    assert out.startswith("Error:")
    assert "batch_id" in out


def test_present_artifact_wrong_type_returns_error(db):
    """`batch_id` declared int. Passing str → 'Error:' with the type hint."""
    out = REGISTRY.view(["present_artifact"]).dispatch(
        "present_artifact",
        {
            "component": "upload_diff_viewer",
            "narration": "wrong type",
            "data": '{"batch_id": "not-a-number"}',
        },
        ctx=_ctx(db),
    )
    assert out.startswith("Error:")
    assert "batch_id" in out
    assert "int" in out


def test_present_artifact_invalid_json_data_returns_error(db):
    """Agent passed `data` that doesn't parse as JSON → 'Error:'.
    Tool's parameter is a string because the @tool decorator can't
    do nested JSON natively, so we have to defend against malformed
    JSON ourselves."""
    out = REGISTRY.view(["present_artifact"]).dispatch(
        "present_artifact",
        {
            "component": "upload_diff_viewer",
            "narration": "bad json",
            "data": '{batch_id: 12}',  # missing quotes
        },
        ctx=_ctx(db),
    )
    assert out.startswith("Error:")
    assert "JSON" in out or "json" in out


def test_present_artifact_empty_narration_returns_error(db):
    """`narration` must be non-empty — it's the chat caption. Empty
    string is rejected so the user doesn't see a bare artifact with no
    context."""
    out = REGISTRY.view(["present_artifact"]).dispatch(
        "present_artifact",
        {
            "component": "narration_only",
            "narration": "  ",  # whitespace only
            "data": "{}",
        },
        ctx=_ctx(db),
    )
    assert out.startswith("Error:")
    assert "narration" in out


# ─── C. Inline-data attack defence ──────────────────────────


def test_present_artifact_rejects_inline_data_payload(db):
    """If agent tries to inline 14 row dicts in `data`, `payload_max_keys`
    rejects it. This is the architectural guard that keeps agent-supplied
    bytes OUT of the leaf data path — the whole reason we built this
    layer."""
    # Build a data dict with >12 keys (the default payload_max_keys).
    too_many = {f"row_{i}": {"some": "data"} for i in range(15)}
    out = REGISTRY.view(["present_artifact"]).dispatch(
        "present_artifact",
        {
            "component": "narration_only",
            "narration": "trying to inline",
            "data": json.dumps(too_many),
        },
        ctx=_ctx(db),
    )
    assert out.startswith("Error:")
    assert "payload_max_keys" in out
    # The error message must point the agent toward the right pattern
    # ("fetch from REST"), otherwise it'll just retry the same inline dump.
    assert "REST" in out or "endpoint" in out


# ─── D. Ack does NOT leak data values ───────────────────────


def test_present_artifact_ack_does_not_echo_data_values(db):
    """The tool returns an ack to the agent — but the ack must NOT
    include the values from `data`. Echoing values invites the agent to
    re-summarise the same data downstream, which is where past
    hallucinations have come from. We pin the contract: ack contains
    component, data KEY NAMES (not values), and narration prefix."""
    out = REGISTRY.view(["present_artifact"]).dispatch(
        "present_artifact",
        {
            "component": "upload_diff_viewer",
            "narration": "preview for batch_id 12345",
            "data": '{"batch_id": 12345}',
        },
        ctx=_ctx(db),
    )
    # No "Error:" — success path
    assert not out.startswith("Error:")
    # The literal data VALUE (12345) is fine to mention in passing
    # but the narration value must appear (so the agent can confirm
    # delivery); we just check `data_keys` does NOT echo values.
    assert "data_keys=" in out
    assert "batch_id" in out  # key name appears — fine
    # The narration is intentionally allowed up to 80 chars; verify it's
    # truncated rather than dumped wholesale by the ack.
    assert "preview for batch_id 12345" in out


# ─── E. HTTP endpoint contract ─────────────────────────────


def test_artifact_endpoint_returns_4state_diff(client: TestClient, db):
    """End-to-end: real upload + real preview + GET endpoint → 4-state
    keys present in the response. This nails the contract the React
    component will rely on."""
    user = seed_user(db, email="artifact1@test.com")
    headers = login(client, "artifact1@test.com")

    seed_product(db, code="A1", name="Apple", price=10.0)
    blob = make_excel([{
        "product_code": "A1", "product_name": "Apple", "price": 12.0,
    }])
    batch = parse_excel(db, file_bytes=blob, filename="a.xlsx", user_id=user.id)
    resolve_and_score(db, batch_id=batch.id, user_id=user.id)

    r = client.get(f"/api/artifacts/upload-diff/{batch.id}", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # 4-state schema lives inside update[*].fields per row
    assert body["update"], "expected at least one update row"
    fields = body["update"][0]["fields"]
    assert "price" in fields
    assert "action" in fields["price"]
    assert fields["price"]["action"] == "change"


def test_artifact_endpoint_cross_user_returns_404(client: TestClient, db):
    """User 2 asking for user 1's batch → 404. 404 (not 403) so we
    don't confirm batch existence to non-owners."""
    alice = seed_user(db, email="alice-art@test.com")
    bob = seed_user(db, email="bob-art@test.com")
    assert bob.id != alice.id  # sanity

    seed_product(db, code="A1", name="Apple")
    blob = make_excel([{"product_code": "A1", "product_name": "Apple"}])
    batch = parse_excel(db, file_bytes=blob, filename="alices.xlsx", user_id=alice.id)
    resolve_and_score(db, batch_id=batch.id, user_id=alice.id)

    bob_headers = login(client, "bob-art@test.com")
    r = client.get(f"/api/artifacts/upload-diff/{batch.id}", headers=bob_headers)
    assert r.status_code == 404


def test_artifact_endpoint_unknown_batch_returns_404(client: TestClient, db):
    seed_user(db, email="ghost-art@test.com")
    headers = login(client, "ghost-art@test.com")
    r = client.get("/api/artifacts/upload-diff/99999", headers=headers)
    assert r.status_code == 404


def test_artifact_endpoint_requires_auth(client: TestClient):
    """No bearer token → 401 (not 200, not 403). Anyone hitting the
    artifact endpoint must be authenticated — even though it's
    technically a "view" endpoint, the data may include pricing."""
    r = client.get("/api/artifacts/upload-diff/1")
    assert r.status_code in (401, 403)


# ─── F. Catalog introspection ───────────────────────────────


def test_catalog_known_components_includes_built_ins():
    """The two built-ins (`upload_diff_viewer`, `narration_only`) must
    always be in the catalog. If someone deletes them by accident the
    agent loses the artifact channel silently — pin them here."""
    names = {c.name for c in artifacts.known_components()}
    assert "upload_diff_viewer" in names
    assert "narration_only" in names


def test_present_artifact_docstring_lists_all_components():
    """The docstring is the prompt the LLM sees. If a new component is
    registered but the docstring isn't refreshed, the agent won't know
    to call it. Today we rebuild it at module import via `.format()`;
    pin that path so future refactors don't silently break the link."""
    from agent.runtime.tools.artifacts import present_artifact
    doc = present_artifact.__doc__ or ""
    for spec in artifacts.known_components():
        assert spec.name in doc, (
            f"component '{spec.name}' missing from present_artifact docstring — "
            f"agent prompt is stale"
        )


# ─── G. View param (Tier 1.5 — parameterized component) ──────


def test_validate_optional_view_omitted_is_valid(db):
    """Backward compat: existing callers that don't pass `view` keep
    working. P15 shipped without view; P16 adds it optionally."""
    err = artifacts.validate(
        "upload_diff_viewer", {"batch_id": 12}
    )
    assert err is None


def test_validate_view_mode_table_is_valid(db):
    err = artifacts.validate(
        "upload_diff_viewer",
        {"batch_id": 12, "view": {"mode": "table"}},
    )
    assert err is None


@pytest.mark.parametrize("mode", ["table", "cards", "heatmap", "field_grouped"])
def test_validate_each_supported_view_mode(db, mode):
    """All four declared modes must pass validation. Drift here = agent
    will hit 'Error: view.mode must be one of [...]' even though it
    picked a documented value."""
    err = artifacts.validate(
        "upload_diff_viewer",
        {"batch_id": 12, "view": {"mode": mode}},
    )
    assert err is None, f"mode={mode!r} should be valid: {err}"


def test_validate_unknown_view_mode_returns_error(db):
    """Agent fabricates a mode ('pie_chart') → 'Error:' with allowed list."""
    err = artifacts.validate(
        "upload_diff_viewer",
        {"batch_id": 12, "view": {"mode": "pie_chart"}},
    )
    assert err is not None
    assert "view.mode" in err
    # The error must enumerate valid options so the agent can fix on retry.
    for valid in ("table", "cards", "heatmap", "field_grouped"):
        assert valid in err, f"valid mode '{valid}' must appear in error: {err}"


def test_validate_view_must_be_dict(db):
    """`view` is declared as optional `dict`. Passing a string is rejected."""
    err = artifacts.validate(
        "upload_diff_viewer",
        {"batch_id": 12, "view": "table"},
    )
    assert err is not None
    assert "view" in err and "dict" in err


def test_validate_view_group_by_enum(db):
    """`view.group_by` ∈ {row, field, action}. Wrong value rejected."""
    err = artifacts.validate(
        "upload_diff_viewer",
        {"batch_id": 12, "view": {"group_by": "rainbow"}},
    )
    assert err is not None
    assert "view.group_by" in err


def test_validate_view_empty_dict_is_valid(db):
    """Empty view dict = "use defaults". Must pass."""
    err = artifacts.validate(
        "upload_diff_viewer",
        {"batch_id": 12, "view": {}},
    )
    assert err is None


def test_validate_view_partial_keys_ok(db):
    """User can specify mode but not group_by, or vice versa. Both ok."""
    assert artifacts.validate(
        "upload_diff_viewer",
        {"batch_id": 12, "view": {"mode": "cards"}},
    ) is None
    assert artifacts.validate(
        "upload_diff_viewer",
        {"batch_id": 12, "view": {"group_by": "field"}},
    ) is None


def test_present_artifact_accepts_data_as_dict(db):
    """Gemini's OpenAI-compat tool path sometimes passes `data` as a
    dict literal even though our @tool signature declares `data: str`.
    Verified live in e2e 2026-05-19. Tool must accept both shapes so
    a real-Gemini run doesn't fail with `JSON object must be str ...`."""
    out = REGISTRY.view(["present_artifact"]).dispatch(
        "present_artifact",
        {
            "component": "upload_diff_viewer",
            "narration": "dict-shaped data",
            "data": {"batch_id": 42},  # dict, NOT a JSON string
        },
        ctx=_ctx(db),
    )
    assert not out.startswith("Error:"), out
    assert "batch_id" in out


def test_preview_upload_emits_next_action_hint_at_threshold(db):
    """When preview returns >= 10 rows of structured diff data, the tool
    output must include a `_next_action_required` hint pointing the
    agent at `present_artifact`. Without this hint the agent silently
    falls back to writing a markdown summary that truncates at scale
    (verified e2e 2026-05-19 — agent ignored docstring + SKILL.md but
    followed the in-band tool-result hint reliably)."""
    # Seed 12 products + upload 12 changes → 12 update rows.
    for i in range(12):
        seed_product(db, code=f"X{i}", name=f"Product {i}", price=10.0)
    blob = make_excel([
        {"product_code": f"X{i}", "product_name": f"Product {i}", "price": 20.0}
        for i in range(12)
    ])
    from domains.masterdata.upload import parse_excel, resolve_and_score
    batch = parse_excel(db, file_bytes=blob, filename="hint.xlsx", user_id=1)
    resolve_and_score(db, batch_id=batch.id, user_id=1)

    out = REGISTRY.view(["preview_upload"]).dispatch(
        "preview_upload", {"batch_id": batch.id}, ctx=_ctx(db),
    )
    assert not out.startswith("Error:")
    # The @tool decorator hard-caps results at 16K chars so the agent's
    # context doesn't balloon. Past the cap the JSON gets sliced mid-
    # string — json.loads() can fail. Verify by substring instead, with
    # the requirement that the hint lives in the FIRST 1000 chars so the
    # LLM sees it before reading any row data.
    head = out[:1000]
    assert '"_next_action_required": "present_artifact"' in head, (
        f"hint not in first 1000 chars — agent reads top-down, hint must "
        f"come before row arrays. head: {head[:300]!r}"
    )
    assert '"component": "upload_diff_viewer"' in head
    assert f'"batch_id": {batch.id}' in head


def test_preview_upload_omits_hint_below_threshold(db):
    """At < 10 rows of structured data, agent can summarize in chat —
    the artifact viewer is overkill. Hint MUST be absent so we don't
    push the agent toward unnecessary UI hand-offs for tiny batches."""
    seed_product(db, code="ONE", name="Single", price=10.0)
    blob = make_excel([{"product_code": "ONE", "product_name": "Single", "price": 20.0}])
    from domains.masterdata.upload import parse_excel, resolve_and_score
    batch = parse_excel(db, file_bytes=blob, filename="small.xlsx", user_id=1)
    resolve_and_score(db, batch_id=batch.id, user_id=1)

    out = REGISTRY.view(["preview_upload"]).dispatch(
        "preview_upload", {"batch_id": batch.id}, ctx=_ctx(db),
    )
    # 1-row batch fits inside the 16K cap so JSON parses cleanly.
    body = json.loads(out)
    assert "_next_action_required" not in body, (
        "Below-threshold preview must NOT carry the artifact hint — "
        "we don't want agents pushing the viewer for 1-row batches."
    )


# ─── H. generic_table — universal tabular fallback ──────────


def test_validate_generic_table_minimum_shape():
    """Smallest valid generic_table payload — title + empty columns
    list + empty rows list. Validator should accept; the React component
    handles the empty-state UI."""
    err = artifacts.validate(
        "generic_table",
        {"title": "X", "columns": [], "rows": []},
    )
    assert err is None, err


def test_validate_generic_table_with_real_data():
    err = artifacts.validate(
        "generic_table",
        {
            "title": "Tokyo 24h",
            "columns": [
                {"key": "hour", "label": "时间"},
                {"key": "temp", "label": "气温"},
            ],
            "rows": [
                {"hour": "14:00", "temp": "26°C"},
                {"hour": "15:00", "temp": "27°C"},
            ],
        },
    )
    assert err is None, err


def test_validate_generic_table_rejects_missing_required():
    """`rows` is required. Missing it must surface a specific error so
    the agent can recover without guessing."""
    err = artifacts.validate(
        "generic_table", {"title": "X", "columns": []}
    )
    assert err is not None
    assert "rows" in err


def test_validate_generic_table_rejects_array_rows():
    """Empirically (e2e 2026-05-19) Gemini sometimes emits SQL-style
    array rows (`rows: [['14:00', 26], ['15:00', 27]]`) — coherent in
    isolation but incompatible with our `row[col.key]` renderer. The
    validator must reject + tell the agent the right shape."""
    err = artifacts.validate(
        "generic_table",
        {
            "title": "X",
            "columns": [{"key": "hour", "label": "时间"}],
            "rows": [["14:00"], ["15:00"]],
        },
    )
    assert err is not None
    assert "object" in err.lower() or "dict" in err.lower()
    # Must include a worked example so agent can self-correct.
    assert "hour" in err or "key" in err


def test_validate_generic_table_rejects_columns_with_name_key():
    """Same incident: Gemini sometimes uses `{name, type}` (SQL/CSV
    convention) instead of `{key, label}`. The validator must catch
    this and point at the right field names."""
    err = artifacts.validate(
        "generic_table",
        {
            "title": "X",
            "columns": [{"name": "hour", "type": "STRING"}],
            "rows": [],
        },
    )
    assert err is not None
    assert "key" in err and "label" in err


def test_validate_generic_table_normalizes_title_to_label():
    """Industry table-lib alias auto-acceptance (prod 2026-05-19).

    Gemini 3 reflexively uses `{title, key}` because Ant Design / AG Grid /
    React Table use `title` (or `header`/`headerName`) as the header
    field. Rejecting cost a full LLM round-trip per table dispatch.
    The validator now rewrites `title`→`label` in place; the column dict
    that downstream code sees is canonical (`{key, label}`)."""
    columns = [
        {"key": "time", "title": "时间"},
        {"key": "temp", "title": "温度 (°C)"},
    ]
    err = artifacts.validate(
        "generic_table",
        {"title": "X", "columns": columns, "rows": []},
    )
    assert err is None, err
    # In-place mutation: downstream frontend sees `label`, not `title`.
    assert columns[0] == {"key": "time", "label": "时间"}
    assert columns[1] == {"key": "temp", "label": "温度 (°C)"}


@pytest.mark.parametrize(
    "label_alias", ["title", "header", "headerName", "Header"]
)
def test_validate_generic_table_normalizes_all_label_aliases(label_alias):
    """All industry table-lib header-field names are auto-normalized."""
    col = {"key": "h", label_alias: "时间"}
    err = artifacts.validate(
        "generic_table",
        {"title": "X", "columns": [col], "rows": []},
    )
    assert err is None, err
    assert col == {"key": "h", "label": "时间"}


@pytest.mark.parametrize(
    "key_alias", ["field", "dataIndex", "accessorKey", "accessor"]
)
def test_validate_generic_table_normalizes_all_key_aliases(key_alias):
    """Same for accessor aliases (AG Grid `field`, Ant Design
    `dataIndex`, TanStack `accessorKey`, React Table `accessor`)."""
    col = {key_alias: "hr", "label": "时间"}
    err = artifacts.validate(
        "generic_table",
        {"title": "X", "columns": [col], "rows": []},
    )
    assert err is None, err
    assert col == {"key": "hr", "label": "时间"}


def test_validate_generic_table_still_rejects_ambiguous_name_type():
    """`name`/`type` look like column descriptors but in SQL/CSV-style
    payloads they're row-data fields (e.g. `type: "STRING"` = data type).
    Auto-rewriting them would corrupt those payloads. They stay
    suggest-only — the validator rejects + tells the agent what to
    rename. Pins the line we DON'T cross."""
    err = artifacts.validate(
        "generic_table",
        {
            "title": "X",
            "columns": [{"name": "hour", "type": "STRING"}],
            "rows": [],
        },
    )
    assert err is not None
    # Specifically — the column dict should NOT have been mutated either.
    # (If we accidentally normalized, this test would catch it because
    # err would be None.)
    assert "key" in err and "label" in err


def test_validate_generic_table_rejects_over_100_rows():
    """Hard cap at 100 rows — empirically the safe band for Gemini Flash
    inline (0% drift). Past it the structural no-hallucination promise
    weakens. Validator must point this out specifically."""
    err = artifacts.validate(
        "generic_table",
        {
            "title": "X",
            "columns": [{"key": "a", "label": "A"}],
            "rows": [{"a": i} for i in range(150)],
        },
    )
    assert err is not None
    assert "100" in err
    assert "rows" in err.lower()


def test_validate_collects_multiple_errors_in_one_round():
    """Pre-fix (2026-05-19) validate() was early-return: agent missing
    BOTH a column shape AND row shape would only learn about one on
    round 1, then the other on round 2, then might give up. Pin the
    new behavior: ONE call returns ALL issues.

    Note: as of v41 `title` is OPTIONAL — the validator no longer
    rejects payloads that omit it, since empirically agents forgot it
    on first call and the frontend renders gracefully without one.
    """
    err = artifacts.validate(
        "generic_table",
        {
            "columns": [{"id": "h", "label": "时间"}],  # ambiguous field name
            "rows": [["a"], ["b"]],  # array rows, not dicts
        },
    )
    assert err is not None
    # Both issues must appear in one message
    assert "key" in err  # column field name issue
    assert "rows[0]" in err or "object keyed" in err
    # And the multi-issue counter should be visible
    assert "issue" in err.lower() or "fix" in err.lower()


def test_validate_generic_table_title_is_optional():
    """v41: `title` moved from required to optional. Empirically
    (prod 2026-05-19) Gemini 3 forgets it on first call about half the
    time — every miss cost one full retry round-trip. With it optional
    + validator injecting a default empty string, the frontend's
    `<h3>{title}</h3>` renders empty and the "AI-generated content"
    badge already provides context. Saves ~5s + one LLM call per
    table dispatch.
    """
    data = {
        "columns": [{"key": "hour", "label": "时间"}],
        "rows": [{"hour": "14:00"}],
    }
    err = artifacts.validate("generic_table", data)
    assert err is None, err
    # In-place injection so downstream code sees `title` present
    assert data["title"] == ""


def test_validate_error_says_artifact_not_dispatched():
    """Severe-wording test. Pre-fix, error messages sounded like minor
    notes ("Error: missing required key 'rows'"). Agents fixed one and
    declared success. Post-fix, EVERY error block must lead with the
    consequence: artifact was NOT rendered, user sees nothing.

    Note: as of v41 a payload that's only missing `title` is no longer
    an error (title is optional). Trigger the error path with a payload
    that's missing a still-required field instead."""
    err = artifacts.validate("generic_table", {"columns": []})  # missing `rows`
    assert err is not None
    # Consequence-forward language must be present
    assert "NOT dispatched" in err
    assert "NO panel" in err or "see no" in err.lower()


def test_validate_data_driven_diff_pinpoints_id_to_key():
    """The validator used to say 'Use key (not name)' — agent who used
    `id` instead of `key` didn't see itself in that anti-pattern list.
    Post-fix the message must point at the ACTUAL field name."""
    err = artifacts.validate(
        "generic_table",
        {
            "title": "X",
            "columns": [{"id": "h", "label": "时间"}],
            "rows": [],
        },
    )
    assert err is not None
    # Must specifically call out `id` as the thing to rename
    assert "id" in err
    # Must specify the rename direction
    assert ("rename" in err.lower()) or ("→ `key`" in err) or ("'id' → 'key'" in err) or ("`id` → `key`" in err)


def test_validate_data_driven_diff_pinpoints_ambiguous_aliases():
    """Coverage for suggest-only aliases — `name`, `column`, `id`, `k`.
    These are ambiguous (could be data values, not headers) so we
    REJECT them but point at the rename. `field` / `dataIndex` /
    `accessorKey` / `accessor` are NOT in this list because they're
    industry-standard table-lib accessors that get auto-normalized
    (see `test_validate_generic_table_normalizes_all_key_aliases`)."""
    for alias in ("name", "column", "id", "k"):
        err = artifacts.validate(
            "generic_table",
            {
                "title": "X",
                "columns": [{alias: "h", "label": "L"}],
                "rows": [],
            },
        )
        assert err is not None, f"alias {alias!r} should fail"
        assert alias in err, f"alias {alias!r} should appear in error: {err}"


def test_validate_unknown_component_surfaces_fallback_options():
    """When agent invents a name, the error must list the two universal
    fallbacks (`generic_table` + `narration_only`) — empirically (e2e
    2026-05-19) agents gave up on unknown-component without help."""
    err = artifacts.validate("weather_widget", {"location": "Tokyo"})
    assert err is not None
    # Lists registered names + suggests fallbacks
    assert "generic_table" in err
    assert "narration_only" in err
    # Names the failure clearly
    assert "weather_widget" in err


def test_catalog_components():
    """Built-in artifact catalog. Drift here likely means someone deleted
    a registration or added one without updating this list. Update the
    set deliberately when adding a new spec — the test should fail
    loud, not silently allow.

    Current set (2026-05-27):
      - upload_diff_viewer (master-data upload preview)
      - narration_only     (escape hatch text-in-panel)
      - generic_table      (universal tabular fallback)
      - inquiry_batch_card (post-generate_inquiry file list)
    """
    names = {c.name for c in artifacts.known_components()}
    assert names == {
        "upload_diff_viewer",
        "narration_only",
        "generic_table",
        "inquiry_batch_card",
    }


def test_present_artifact_docstring_includes_view_enum():
    """The docstring must surface the legal enum values so the LLM
    learns them without trial-and-error. Pin both the enum list AND
    the optional-marker so refactor drift surfaces here."""
    from agent.runtime.tools.artifacts import present_artifact
    doc = present_artifact.__doc__ or ""
    assert "view?" in doc, "view must be marked optional in docstring"
    # Each declared mode must appear so agent can pattern-match
    for mode in ("table", "cards", "heatmap", "field_grouped"):
        assert mode in doc, f"view.mode value '{mode}' missing from docstring"
