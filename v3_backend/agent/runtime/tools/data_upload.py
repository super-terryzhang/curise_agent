"""Data-upload tools — drive a master-data batch import from chat.

User flow:
  1. Frontend uploads file to `POST /api/data-upload/upload` → batch_id
  2. User says "process batch_id=X" in chat
  3. Agent calls `parse_uploaded_file(batch_id)` → resolves matches, returns stats
  4. Agent calls `preview_upload(batch_id)` → shows the diff
  5. Agent presents summary, then calls `propose_action(action="commit_upload_batch", ...)`
  6. User clicks "approve" in UI → backend dispatches `commit_batch`

The tool functions are thin wrappers over `domains.masterdata.upload`.
Commit + rollback are deliberately Tier-2 (propose only) — they're
bulk DB writes by definition.
"""

from __future__ import annotations

import json
import os

from pydantic import BaseModel, Field

from agent.runtime.approvals import ActionSpec, register_action
from agent.runtime.deps import get_deps
from domains.masterdata import upload as upload_service
from domains.masterdata.upload.errors import UploadError
from general_agent import ToolContext, tool


# Public base URL the user's browser will hit to download the template.
# Set in prod via env (`PUBLIC_BACKEND_URL`); dev / test runners fall
# back to a relative path so the chat UI can still render a clickable
# link via its frontend reverse proxy.
def _public_backend_url() -> str:
    return os.environ.get("PUBLIC_BACKEND_URL", "").rstrip("/")


@tool(toolset="business", emoji="📋")
def get_upload_template(*, ctx: ToolContext) -> str:  # noqa: ARG001 — ctx required by tool decorator
    """Produce the product-upload Excel template (download link + column reference).

    **CALL THIS AS YOUR FIRST ACTION** whenever the user wants to
    upload / update / import / batch-modify product data AND has not
    yet attached a file or supplied a batch_id. The returned markdown
    contains the actual download URL — without calling this tool you
    have NO way to give the user the file (saying "please download
    the template" without the link is useless).

    Typical triggers:
      - "我想上传产品" / "我要批量更新" / "怎么导入新产品"
      - "我有一份 excel 想上传"
      - "上传产品数据" / "import products" / "batch update prices"

    Skip ONLY when the user has already attached a file in this turn
    or referenced an existing batch_id.

    Returns markdown — paste it verbatim into your reply (the chat UI
    renders markdown, including the clickable link).
    """
    base = _public_backend_url()
    # Empty base → relative URL; the frontend's reverse proxy maps
    # `/api/...` onto the backend. Production sets PUBLIC_BACKEND_URL.
    download_url = f"{base}/api/data-upload/template" if base else "/api/data-upload/template"

    body = (
        "📋 **产品上传模板**\n\n"
        f"[📥 下载模板（product_upload_template.xlsx）]({download_url})\n\n"
        "**模板说明** —— 工作表「产品数据」共 21 列；"
        "`product_name`、`country`、`port` 必填，其余按需填写。下面是常用 8 列：\n\n"
        "| 列名 | 必填? | 示例 | 备注 |\n"
        "| --- | --- | --- | --- |\n"
        "| `product_name` | ✅ **必填** | Apple - Red Delicious | 英文品名 |\n"
        "| `country` | ✅ **必填** | Japan / USA | 采购国名称（产品身份键之一）|\n"
        "| `port` | ✅ **必填** | Yokohama / Tokyo / LAX | 采购港口名称（产品身份键之一）|\n"
        "| `product_code` | 可选 | FRT-APL-RED 或 00100 | SKU；列已设为文本格式以保留前导零 |\n"
        "| `supplier` | 可选 | Sunkist Growers Inc. | 供应商名称（不是 ID） |\n"
        "| `price` | 可选 | 850.00 | 采购价；直接写数字，不要带 ¥ $ 符号 |\n"
        "| `contract_price` | 可选 | 1050.00 | 卖价，用于对比 PO 单价；空白保留旧值 |\n"
        "| `currency` | 可选 | USD / JPY | ISO 4217 三字母代码 |\n\n"
        "**完整 21 列说明**请看模板内的「使用说明」工作表。"
        "其他可选列：`product_name_jp`、`brand`、`category`、"
        "`unit`、`unit_size`、`pack_size`、`country_of_origin`、"
        "`purchase_price_effective_from`、`purchase_price_effective_to`、"
        "`selling_price_effective_from`、`selling_price_effective_to`、"
        "`effective_from`、`effective_to`。\n\n"
        "⚠️ **为什么 country + port 必填** —— 同一个产品代码在不同港口是**不同的记录**"
        "（价格、币种、供应商常常不同）。系统按 `(country, port, code/name)` 三元组识别产品；"
        "缺 country 或 port 的行会直接报 row-level 错误，不会被静默匹配到其他港口的记录。\n\n"
        "**使用方法**：\n"
        "1. 下载模板，删除示例 3 行，填入你自己的产品\n"
        "2. 保存为 `.xlsx` 后，**直接把文件拖进聊天框**\n"
        "3. 上传成功后会拿到 `batch_id`，我会引导你预览 + 确认 + 提交"
    )
    # The tool wrapper expects a string; the agent will paste the body
    # into its turn output and the markdown renderer handles the rest.
    return body


@tool(toolset="business", emoji="📥")
def list_my_uploads(
    status: str = "",
    has_errors: bool = False,
    limit: int = 10,
    *,
    ctx: ToolContext,
) -> str:
    """List the user's recent data-upload batches.

    Two orthogonal filters — pick whichever matches what the user
    actually wants:

      • `status` — batch-level lifecycle state. Valid values:
        parsing / ready / resolved / completed / rolled_back / failed.
        `status="failed"` specifically means the batch CRASHED during
        parsing or resolution (very rare). Empty string = no status
        filter.

      • `has_errors` — `True` returns only batches where some rows had
        validation errors (`error_rows > 0`), regardless of batch
        status. This is what users almost always mean by phrases like
        "失败的上传 / 失败的批次 / 出错的批次 / failed uploads /
        batches with errors" — because the BATCH usually succeeded
        end-to-end, but individual ROWS were rejected. Default False.

    ⚠️ Disambiguation rule of thumb: if the user says anything fuzzy
    like "失败", "有问题", "出错", "failed", "broken" — use
    `has_errors=True`, NOT `status="failed"`. They mean row-level
    errors. Only use `status="failed"` if the user literally asked
    "did the batch itself crash".

    Each returned item already carries `error_rows`, `status`,
    `total_rows`, `new_rows` etc. — drill into specific batches with
    `preview_upload(batch_id)` for the per-row error details.

    Args:
        status: Batch-level status filter (see above). Empty = all.
        has_errors: If True, only batches with `error_rows > 0`.
        limit: Max rows (1-50, default 10).
    """
    deps = get_deps(ctx)
    bounded = max(1, min(int(limit) if limit else 10, 50))

    # ARCH NOTE: thin wrapper around upload_service.search_batches, which
    # provides the true DB COUNT alongside the limited items list. Do not
    # re-introduce direct SQL here (ADR-0002 + check_arch RULE-2).
    try:
        result = upload_service.search_batches(
            deps.db,
            user_id=deps.user_id,
            limit=bounded,
            status=status or None,
            has_errors=bool(has_errors),
        )
    except UploadError as exc:
        return f"Error: {exc}"
    total_matching = int(result.get("total", 0))
    rows = result.get("items") or []
    payload: dict = {
        "total_matching": total_matching,
        "returned": len(rows),
        "truncated": total_matching > len(rows),
        "items": rows,
    }
    if "hint" in result:
        payload["hint"] = result["hint"]
    return json.dumps(payload, ensure_ascii=False, default=str)


@tool(toolset="business", emoji="📥")
def parse_uploaded_file(batch_id: int, *, ctx: ToolContext) -> str:
    """Resolve and score a freshly-uploaded batch.

    The user's file is already parsed (the HTTP upload endpoint did
    that). This tool runs the matching pass — comparing every staging
    row to live products. Returns batch summary including how many
    rows would create / update / are conflicts.

    Args:
        batch_id: Numeric batch id returned from `/api/data-upload/upload`.
    """
    deps = get_deps(ctx)
    try:
        batch = upload_service.resolve_and_score(
            deps.db, batch_id=batch_id, user_id=deps.user_id
        )
    except UploadError as exc:
        return f"Error: {exc}"
    summary = {
        "batch_id": batch.id,
        "filename": batch.filename,
        "status": batch.status,
        "total_rows": batch.total_rows,
        "matched_exact": batch.matched_exact,
        "matched_fuzzy": batch.matched_fuzzy,
        "new_rows": batch.new_rows,
        "error_rows": batch.error_rows,
    }
    return json.dumps(summary, ensure_ascii=False, default=str)


@tool(toolset="business", emoji="📥")
def preview_upload(batch_id: int, limit: int = 50, *, ctx: ToolContext) -> str:
    """Show the diff for an already-resolved batch.

    Returns groups: create / update / skip / error. Each `update` row's
    `fields` dict carries the 4-state schema for ALL 19 mutable fields
    (action ∈ {change, unchanged, keep_db, set_new}, plus db/excel
    values and a `will_write` boolean). `skip` rows also include the
    same `fields` dict so the agent can answer "why was this row
    skipped" without guessing — typically the answer is "all values
    matched the DB already".

    Each row also has a convenience `will_write_fields` list of just
    the field names where action ∈ {change, set_new}.

    For row-specific drill-down (user asks "what about row 5
    specifically"), call `inspect_upload_row(batch_id, row_index)`
    instead — it returns identity context + unrecognised Excel columns
    that this aggregate view doesn't surface.

    Args:
        batch_id: Numeric batch id.
        limit: Max rows per group (default 50).
    """
    deps = get_deps(ctx)
    bounded = max(1, min(int(limit) if limit else 50, 200))
    try:
        out = upload_service.preview_changes(
            deps.db, batch_id=batch_id, user_id=deps.user_id, limit=bounded
        )
    except UploadError as exc:
        return f"Error: {exc}"

    # In-band next-action hint. Empirically (e2e + reliability probes
    # 2026-05-19), in the crowded v3 chat toolset the agent's tool-
    # selection signal for `present_artifact` is weak: it sees the JSON,
    # then `finish`es with a markdown summary that silently truncates at
    # ≥ 100 rows. Inlining the directive into the tool's own result
    # lifts adoption from 0% to (target) ≥ 80% — the result is the
    # LAST thing the agent reads before deciding the next step, so it
    # has higher weight than docstrings or system-prompt rules.
    update_count = (out.get("summary") or {}).get("update") or 0
    create_count = (out.get("summary") or {}).get("create") or 0
    structured_row_count = update_count + create_count
    if structured_row_count >= 10:
        # Prepend so the agent reads the directive BEFORE wading through
        # the row arrays — JSON dicts preserve insertion order, and the
        # tool result is the agent's last input before its next decision.
        hint = {
            "_next_action_required": "present_artifact",
            "_next_action_args": {
                "component": "upload_diff_viewer",
                "data": {"batch_id": batch_id},
                "narration": (
                    "<replace with 1 concrete sentence referencing real "
                    "row counts + the most surprising change>"
                ),
            },
            "_why": (
                f"This preview has {structured_row_count} rows of structured "
                f"diff data — past the 10-row threshold. Markdown summaries "
                f"silently truncate at ≥100 rows (measured 44.5% row loss at "
                f"200 rows). The user already sees the data via the artifact "
                f"viewer; your chat reply should be SHORT — call "
                f"`present_artifact` immediately, then `finish` with one "
                f"sentence pointing the user at the viewer. Do NOT paste "
                f"the diff as markdown. Available view modes: table / cards "
                f"/ heatmap / field_grouped — pick by user wording."
            ),
        }
        out = {**hint, **out}
    return json.dumps(out, ensure_ascii=False, default=str)


@tool(toolset="business", emoji="🔍")
def inspect_upload_row(
    batch_id: int, row_index: int, *, ctx: ToolContext
) -> str:
    """Return the complete 4-state field-by-field state for ONE row of a
    resolved batch. Use this whenever the user asks a row-specific
    question — never answer from memory of an earlier preview output.

    Output shape:
        {
          "batch_id": N,
          "row_index": M,
          "match_status": "exact"/"name_exact"/"fuzzy"/"new"/"error",
          "matched_product_id": int | null,
          "identity": {                 # product_name_en + code, immutable
            "product_name_en": {action: "immutable", db, excel, will_write},
            "code":            {action: "immutable", db, excel, will_write},
          },
          "fields": {                   # 19 mutable fields, 4-state each
            "price":           {action, db, excel, will_write},
            "unit":            {action, db, excel, will_write},
            "pack_size":       {action, db, excel, will_write},
            "product_name_jp": {...},
            "brand":           {...},
            "unit_size":       {...},
            "country_of_origin": {...},
            "currency":        {...},
            "category_id":     {...},
            "supplier_id":     {...},
            "country_id":      {...},
            "port_id":         {...},
            "effective_from":  {...},
            "effective_to":    {...},
            "purchase_price_effective_from": {...},
            "purchase_price_effective_to":   {...},
            "selling_price_effective_from":  {...},
            "selling_price_effective_to":    {...},
          },
          "unrecognized_columns": ["..."],   # Excel headers not mapped
          "validation_errors":   ["..."],    # per-row error messages
        }

    `action` semantics:
      - `change`     — Excel value differs from DB; commit will write
      - `unchanged`  — Excel value equals DB value; commit will NOT write
      - `keep_db`    — Excel cell empty/missing; DB value preserved
      - `set_new`    — DB is NULL/empty, Excel provided value; commit writes
      - `immutable`  — identity field; commit never touches it

    Args:
        batch_id: Numeric batch id.
        row_index: 1-based row in the source Excel.
    """
    deps = get_deps(ctx)
    try:
        out = upload_service.inspect_row(
            deps.db, batch_id=batch_id, row_index=int(row_index),
            user_id=deps.user_id,
        )
    except UploadError as exc:
        return f"Error: {exc}"
    return json.dumps(out, ensure_ascii=False, default=str)


# ─── Tier-2 dispatch handlers (schema-typed, v42+) ───────────


class BatchActionArgs(BaseModel):
    """Args for `commit_upload_batch` / `rollback_upload_batch`.

    The batch_id is the canonical source of truth — `propose_action`
    validates this BEFORE writing the approval row, so a malformed call
    (e.g. Gemini-3 omitting the field) surfaces as an Error string the
    agent self-corrects from, instead of a `status=failed` row polluting
    the approval queue (prod 2026-05-20 Action #8 root cause).
    """

    batch_id: int = Field(..., gt=0, description="UploadBatch.id to operate on")


def _dispatch_commit_upload_batch(deps, args: BatchActionArgs) -> dict:  # noqa: ANN001
    return upload_service.commit_batch(
        deps.db, batch_id=args.batch_id, user_id=deps.user_id
    )


def _dispatch_rollback_upload_batch(deps, args: BatchActionArgs) -> dict:  # noqa: ANN001
    return upload_service.rollback_batch(
        deps.db, batch_id=args.batch_id, user_id=deps.user_id
    )


def _dispatch_cancel_upload_batch(deps, args: BatchActionArgs) -> dict:  # noqa: ANN001
    return upload_service.cancel_batch(
        deps.db, batch_id=args.batch_id, user_id=deps.user_id
    )


register_action(
    ActionSpec(
        name="commit_upload_batch",
        target_kind="upload_batch",
        description="Apply a resolved data-upload batch to the products table.",
        dispatch=_dispatch_commit_upload_batch,
        args_schema=BatchActionArgs,
        target_id_from_args=lambda a: a.batch_id,
    )
)
register_action(
    ActionSpec(
        name="cancel_upload_batch",
        target_kind="upload_batch",
        description=(
            "Discard a resolved-but-uncommitted data-upload batch (user "
            "declined before commit). Use this for 'cancel/取消' on a "
            "batch still at status=resolved. For undoing an *already "
            "committed* batch, use `rollback_upload_batch` instead."
        ),
        dispatch=_dispatch_cancel_upload_batch,
        args_schema=BatchActionArgs,
        target_id_from_args=lambda a: a.batch_id,
    )
)
register_action(
    ActionSpec(
        name="rollback_upload_batch",
        target_kind="upload_batch",
        description=(
            "Reverse a COMMITTED data-upload batch via the changelog. "
            "Only valid when status=completed. For a still-resolved "
            "(uncommitted) batch, use `cancel_upload_batch`."
        ),
        dispatch=_dispatch_rollback_upload_batch,
        args_schema=BatchActionArgs,
        target_id_from_args=lambda a: a.batch_id,
    )
)
