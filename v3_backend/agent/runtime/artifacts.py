"""Artifact catalog — the registry of UI components the agent can ask the
frontend to render.

Why this exists:
    Agents are bad at writing HTML bytes (we measured 2.83% cell drift at
    200 rows and silent truncation at 50 rows multi-turn — see
    `scripts/probe_html_hallucination.py`). The industry-standard cure
    (Google A2UI, Vercel JSON-Render, CopilotKit AG-UI) is a `declarative
    generative UI` pattern: the agent never produces UI bytes; it picks a
    pre-registered component name and supplies a small reference payload.
    Frontend has the implementation. Backend supplies the data via REST.

How it works:
    1. `register(name, schema, ...)` — backend declares a component is
       available. Each component has a JSON-schema-like dict mapping
       required keys → expected python types.
    2. `validate(name, data) -> str | None` — returns None on success,
       error string on rejection (unknown name, missing keys, wrong type).
       The agent tool calls this before dispatching.
    3. `known_components()` — used by tool docstring auto-generation so
       the agent's prompt always lists the current catalog.

The catalog is intentionally small. Start with the components we need;
let the file grow as new chat-driven views (order rematch, supplier
diff, inquiry progress) ship. Each addition is one entry here + one
React component in v3-frontend.

Security model (Anthropic / A2UI converged): agents can NEVER request a
component name not in this catalog. Schema validation rejects payloads
that try to inline large arrays of values — the agent can only supply
the small reference keys declared in `schema`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ComponentSpec:
    """Catalog entry for one render-able UI component.

    Attributes:
        name: Stable identifier the agent supplies. Snake_case, no spaces.
        description: One-line purpose, surfaced in `present_artifact`
            tool docstring so the agent learns what to use it for.
        schema: Required keys → expected python type (or tuple of types).
        optional_schema: Optional keys → expected type. Missing OK; if
            present must match the type.
        enums: Dot-path → allowed values. Path "view.mode" means: when
            `data["view"]` is a dict that contains key "mode", its value
            must be in the tuple. Missing paths are not validated (the
            optional_schema rule applies first to check structure).
        payload_max_keys: Soft cap on number of top-level keys. Catches
            "agent dumped 200 rows here". Default 12.
    """

    name: str
    description: str
    schema: dict[str, Any] = field(default_factory=dict)
    optional_schema: dict[str, Any] = field(default_factory=dict)
    enums: dict[str, tuple[str, ...]] = field(default_factory=dict)
    payload_max_keys: int = 12


_CATALOG: dict[str, ComponentSpec] = {}


def register(spec: ComponentSpec) -> None:
    """Add a component spec. Re-registering the same name is allowed
    (idempotent at import time) but the new spec replaces the old —
    needed for tests that monkey-patch."""
    _CATALOG[spec.name] = spec


def get_spec(name: str) -> ComponentSpec | None:
    return _CATALOG.get(name)


def known_components() -> list[ComponentSpec]:
    return sorted(_CATALOG.values(), key=lambda s: s.name)


# Two-tier alias model:
#
# 1. **Auto-normalize** (narrow, safe): widely-used React table-library
#    field names (Ant Design, AG Grid, React Table, TanStack, Material
#    UI). When agents reflexively use these — empirically Gemini 3
#    does — we rewrite the column dict in place to canonical
#    `{key, label}` instead of rejecting. The frontend keys off the
#    canonical shape, so normalization is invisible downstream.
#
# 2. **Suggest only** (broader): ambiguous names (`name`, `type`, `id`)
#    that could be either headers OR row-data values — auto-renaming
#    them risks mangling SQL/CSV-style payloads where `type: "STRING"`
#    is a column data-type, not a header label. We still mention them
#    in fix suggestions so the agent knows what to change.
_COLUMN_KEY_NORMALIZE = {"field", "dataIndex", "accessorKey", "accessor"}
_COLUMN_LABEL_NORMALIZE = {"title", "header", "headerName", "Header"}

_COLUMN_KEY_ALIASES = _COLUMN_KEY_NORMALIZE | {
    "id", "name", "column", "col", "k",
}
_COLUMN_LABEL_ALIASES = _COLUMN_LABEL_NORMALIZE | {
    "type", "display", "text", "l",
}


def _suggest_column_fix(col: dict[str, Any]) -> str:
    """Return a one-sentence specific renaming instruction based on the
    agent's actual column keys, plus a canonical example. Never refer to
    aliases the agent didn't actually use."""
    given_keys = set(col.keys())
    fixes: list[str] = []
    if "key" not in given_keys:
        for alias in _COLUMN_KEY_ALIASES:
            if alias in given_keys:
                fixes.append(f"rename `{alias}` → `key`")
                break
    if "label" not in given_keys:
        for alias in _COLUMN_LABEL_ALIASES:
            if alias in given_keys:
                fixes.append(f"rename `{alias}` → `label`")
                break
    prefix = ("Specific fix: " + "; ".join(fixes) + ". ") if fixes else ""
    return (
        prefix
        + 'Required shape: {"key": "<unique id>", "label": "<header text>"}. '
        + 'Example: {"key": "hour", "label": "时间"}.'
    )


def _format_errors(name: str, errors: list[str]) -> str:
    """Wrap collected errors with a header that makes the consequence
    explicit. Empirically (prod 2026-05-19) agents that got soft-tone
    errors ("Error: missing required key 'title'") fixed one issue and
    called `finish` claiming success — never noticing the artifact was
    NEVER dispatched. The "User sees NO panel" framing forces the agent
    to keep retrying instead of declaring victory."""
    header = (
        f"⚠️ Artifact NOT dispatched for component '{name}'. "
        f"The user will see NO panel until you fix and retry. "
        f"{len(errors)} issue(s) to fix:"
    )
    body = "\n".join(f"  {i + 1}. {e}" for i, e in enumerate(errors))
    footer = (
        "\nFix ALL issues and call `present_artifact` again. "
        "If you call `finish` without first emitting a successful "
        "`present_artifact`, the user will see only your chat text and "
        "no panel."
    )
    return f"{header}\n{body}{footer}"


def validate(name: str, data: dict[str, Any]) -> str | None:
    """Returns None when valid; an error string otherwise.

    Design:
      - Single "unknown component" → fatal, return immediately with
        fallback suggestions.
      - Otherwise, COLLECT ALL errors (don't early-return). The agent
        only gets one round-trip per validation call; one-shot diagnosis
        beats N retries. Empirically (prod 2026-05-19) the per-error
        early-return cost the agent 3 round-trips on what should have
        been a single fix.
      - Use data-driven diffs (look at agent's actual input, not static
        anti-pattern lists).
      - Lead error block with consequence ("Artifact NOT dispatched.
        User sees NO panel.") to prevent false-success claims.
    """
    spec = _CATALOG.get(name)
    if spec is None:
        known = ", ".join(s.name for s in known_components()) or "(none registered)"
        return (
            f"⚠️ Artifact NOT dispatched — unknown component '{name}'. "
            f"The user will see NO panel until you call again with a valid "
            f"component name. Valid components: {known}.\n"
            f"Fallback options:\n"
            f"  · For tabular data: retry with component='generic_table', "
            f"data={{title, columns: [{{key, label}}], rows: [...]}}.\n"
            f"  · For text-only answer in a richer panel: retry with "
            f"component='narration_only' and put your answer in `narration`.\n"
            f"  · For a plain chat reply (no artifact): skip "
            f"present_artifact and call `finish` with markdown directly."
        )

    if not isinstance(data, dict):
        return _format_errors(
            name,
            [f"data must be a JSON object, got {type(data).__name__}: {data!r}"],
        )

    errors: list[str] = []

    def _check_type(value: Any, expected: Any, key: str) -> str | None:
        if expected is Any:
            return None
        if not isinstance(value, expected):
            type_names = (
                expected.__name__
                if isinstance(expected, type)
                else "/".join(t.__name__ for t in expected)
            )
            return (
                f"key '{key}' for component '{name}' must be {type_names}, "
                f"got {type(value).__name__}"
            )
        return None

    # Required keys — collect missing & type issues; do NOT early-return.
    for key, expected_type in spec.schema.items():
        if key not in data:
            errors.append(
                f"missing required key '{key}' (expected "
                f"{expected_type.__name__ if isinstance(expected_type, type) else expected_type})"
            )
            continue
        err = _check_type(data[key], expected_type, key)
        if err is not None:
            errors.append(err)

    # Optional keys
    for key, expected_type in spec.optional_schema.items():
        if key not in data:
            continue
        err = _check_type(data[key], expected_type, key)
        if err is not None:
            errors.append(err)

    # Enums — dot-path lookup
    for path, allowed in spec.enums.items():
        parts = path.split(".")
        cursor: Any = data
        for p in parts:
            if not isinstance(cursor, dict) or p not in cursor:
                cursor = None
                break
            cursor = cursor[p]
        if cursor is None:
            continue
        if cursor not in allowed:
            errors.append(
                f"'{path}' must be one of {list(allowed)}, got {cursor!r}"
            )

    # Soft cap on top-level keys (for DOMAIN components that should fetch
    # from REST, not inline).
    if len(data) > spec.payload_max_keys:
        errors.append(
            f"data dict has {len(data)} keys, exceeds payload_max_keys="
            f"{spec.payload_max_keys} for component '{name}'. "
            f"Pass reference IDs only; the frontend fetches full data "
            f"via the artifact's REST endpoint."
        )

    # Component-specific guards
    if name == "generic_table":
        # Inject a placeholder title when absent. Frontend renders an
        # "AI-generated content" badge regardless, so an empty header is
        # graceful; explicit empty string keeps the GenericTable.tsx
        # `<h3>` element stable rather than failing TS strictness.
        if "title" not in data:
            data["title"] = ""
        columns = data.get("columns")
        if isinstance(columns, list):
            for i, col in enumerate(columns):
                if not isinstance(col, dict):
                    errors.append(
                        f"columns[{i}] must be a JSON object with `key` + "
                        f"`label`. Got {type(col).__name__}: {col!r}. "
                        + _suggest_column_fix({})
                    )
                    continue
                # Auto-normalize industry-standard column field names to our
                # canonical schema. Empirically (prod 2026-05-19) Gemini 3
                # defaults to `title` because that's what Ant Design / AG
                # Grid / React Table use in its training data. Rejecting
                # those costs a retry round-trip every time — accept the
                # alias instead. Mutates `col` in place so the downstream
                # frontend (which keys off `label`/`key`) sees the
                # canonical shape. Only the narrow `_NORMALIZE` set is
                # auto-renamed; `name`/`type`/`id` stay in suggest-only
                # mode to avoid rewriting SQL/CSV-style payloads.
                if "key" not in col:
                    for alias in _COLUMN_KEY_NORMALIZE:
                        if alias in col:
                            col["key"] = col.pop(alias)
                            break
                if "label" not in col:
                    for alias in _COLUMN_LABEL_NORMALIZE:
                        if alias in col:
                            col["label"] = col.pop(alias)
                            break
                if "key" not in col or "label" not in col:
                    errors.append(
                        f"columns[{i}] missing `key` or `label`. "
                        f"You provided {dict(col)}. "
                        + _suggest_column_fix(col)
                        + " Also remember: use `key` (not `name`) as the "
                        + "row-dict identifier and `label` (not `type`) "
                        + "as the visible header text."
                    )

        rows = data.get("rows")
        if isinstance(rows, list):
            if len(rows) > 100:
                errors.append(
                    f"received {len(rows)} rows, exceeds the 100-row safe "
                    f"cap (measured Gemini Flash drift starts at ≥200 "
                    f"rows). Paginate, summarize, or filter the rows "
                    f"before rendering. For larger datasets backed by the "
                    f"DB, create a domain-specific component with a REST "
                    f"endpoint."
                )
            if rows and not isinstance(rows[0], dict):
                errors.append(
                    f"rows[0] must be a JSON object keyed by column.key, "
                    f"NOT an array. Got {type(rows[0]).__name__}: "
                    f"{rows[0]!r}. Example: "
                    f'rows=[{{"hour": "14:00", "temp": 26}}, ...] where '
                    f'each row key matches a column.key declared above.'
                )

    if not errors:
        return None
    return _format_errors(name, errors)


# ─── Built-in components ─────────────────────────────────────


# upload_diff_viewer — the v0 use case. Renders the full 4-state diff
# (the data the 2026-05-19 incident hid from the agent). Agent supplies
# a `batch_id`; frontend hits GET /api/artifacts/upload-diff/{batch_id}
# to fetch the actual preview JSON.
register(ComponentSpec(
    name="upload_diff_viewer",
    description=(
        "Full 4-state diff view of a master-data upload batch. Supports "
        "multiple view modes — pick the one matching the user's intent. "
        "Use this in Step 4 of the master-data-upload skill instead of "
        "writing a markdown table — agent-generated tables drift at >100 "
        "rows and silently truncate; the viewer is deterministic."
    ),
    schema={"batch_id": int},
    optional_schema={"view": dict},
    enums={
        # view.mode picks the visual layout. All modes show the same
        # underlying 4-state data; only the presentation differs.
        #   - 'table'         — row × field grid (best for ≤ 30 rows or
        #                       large screens). The default if unset.
        #   - 'cards'         — one card per row, expandable. Mobile-
        #                       friendly; best for ≤ 50 rows.
        #   - 'heatmap'       — row × field colored by action; no values
        #                       shown until hover. Best for big batches
        #                       (200+ rows) where the user wants a
        #                       bird's-eye view of which cells change.
        #   - 'field_grouped' — pivots: one section per field, showing
        #                       which rows change that field. Best when
        #                       the user asks "which fields will move?"
        "view.mode": ("table", "cards", "heatmap", "field_grouped"),
        # view.group_by — secondary grouping within table/heatmap modes.
        "view.group_by": ("row", "field", "action"),
    },
))


# narration_only — escape hatch. Agent just wants to talk; no rich
# artifact needed. Lets `present_artifact` stay the single hand-off
# entrypoint without forcing agent to invent a component for plain text.
register(ComponentSpec(
    name="narration_only",
    description=(
        "Pure-text fallback artifact (no rich UI). Use when the answer "
        "is fundamentally text but you want it in the artifact panel "
        "instead of inline chat. For most plain answers, just call "
        "`finish` with markdown — don't reach for this."
    ),
    schema={},
))


# inquiry_batch_card — summary card for a batch of generated supplier
# inquiry Excel files. Surfaces the file list (with per-supplier download
# URLs) in the workspace pane after `generate_inquiry` completes. The
# tool's docstring instructs the agent to dispatch this artifact on
# success — keeps the "AI made these files" moment visible in one place
# instead of forcing the user to navigate to the order detail page.
register(ComponentSpec(
    name="inquiry_batch_card",
    description=(
        "Summary card listing the inquiry Excel files generated for an "
        "order, with per-supplier download URLs. Dispatch this AFTER "
        "`generate_inquiry` or `regenerate_supplier_inquiry` returns a "
        "successful state with at least one generated file. Pass only "
        "`order_id`; the frontend hits /api/artifacts/inquiry-batch/{id} "
        "to fetch the live file list (so reload-after-update reflects the "
        "latest set without a fresh artifact dispatch). Skip this on "
        "pure failure runs (no files = nothing to show)."
    ),
    schema={"order_id": int},
    optional_schema={},
))


# generic_table — the universal catch-all for any tabular answer NOT
# covered by a domain-specific component (e.g. weather hourly forecast,
# search-result list, free-form lookup). Unlike upload_diff_viewer (which
# fetches data server-side from a REST endpoint), this component accepts
# the columns and rows INLINE in the artifact event. That trades the
# zero-hallucination guarantee for general-purpose reach.
#
# Why this is a deliberate hybrid:
#   - For mission-critical data (uploads, financial diffs), domain
#     components fetch from REST → 0% drift, structural guarantee.
#   - For general data (weather, web-search summary, ad-hoc lookups),
#     no fixed backend representation exists. The pragmatic option is
#     agent-inlined rows, capped at sizes where empirical drift is 0%
#     (≤ 100 rows on Gemini 2.5 Flash, measured 2026-05-19; see
#     scripts/probe_html_hallucination.py).
#   - The frontend marks the panel as "AI-generated content" so the
#     user knows the rows came from the model, not a verified backend.
register(ComponentSpec(
    name="generic_table",
    description=(
        "Universal tabular viewer for any 2D data not covered by a "
        "domain-specific component. Use for weather forecasts, search "
        "results, ad-hoc listings, comparisons, statistics. Rows are "
        "agent-supplied so the panel shows an 'AI-generated' badge. "
        "Cap: 100 rows. EXACT data shape: "
        '`{title: str, columns: [{key, label, align?}], rows: [{<col.key>: any, ...}], caption?: str, highlight_key?: str}`. '
        "Column objects use `key` (NOT `name`) + `label` (NOT `type`); "
        "rows are JSON objects keyed by column.key, NOT arrays. "
        "Example: "
        '`{"title": "Weather 24h", '
        '"columns": [{"key":"hour","label":"时间"},{"key":"temp","label":"气温"}], '
        '"rows": [{"hour":"14:00","temp":"26°C"},{"hour":"15:00","temp":"27°C"}]}`. '
        "For mission-critical / financial data with a server-side source, "
        "prefer the matching domain component instead."
    ),
    schema={
        # `title` was required pre-v41 — empirically (prod 2026-05-19)
        # Gemini 3 often omits it on first call (most table libs treat
        # the table header as optional; only a small fraction of
        # training data has top-level `title` on the data payload). The
        # cost of rejecting was a full LLM retry round-trip. v41 moves
        # `title` to optional and the validator injects a sensible
        # default when absent — frontend already renders an
        # "AI-generated content" badge, so a missing title degrades
        # gracefully.
        "columns": list,  # [{key: str, label: str, align?: 'left'|'right'|'center'}]
        "rows": list,     # [{<column.key>: any, ...}, ...] — up to 100 entries
    },
    optional_schema={
        "title": str,             # auto-defaulted by validator if missing
        "caption": str,           # one-line subtitle under the title
        "highlight_key": str,     # column.key whose values should be visually emphasized
    },
    # 5 top-level keys × 100 rows is fine; the cap is on dict KEYS at
    # the top level, not list elements. Each row is one element of
    # `rows`, not a top-level key. So payload_max_keys=5 is plenty.
    payload_max_keys=5,
))
