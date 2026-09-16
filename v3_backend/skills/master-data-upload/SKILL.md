---
name: master-data-upload
description: Walk a user through uploading or updating product master-data via Excel. STEP 0 HARD RULE — when the user signals an upload intent (e.g. "我想上传产品" / "批量更新价格" / "I want to import products") and supplies NO batch_id and NO attached file, your VERY FIRST action MUST be a tool call to `get_upload_template`. NEVER invent a download URL or fabricate column names from memory — the tool is the only source of truth for both. Only after the user uploads do you classify intent (insert vs update), run aggregate quality checks, and commit through the HITL approval gate.
triggers:
  - "上传数据"
  - "数据上传"
  - "导入数据"
  - "上传excel"
  - "上传xlsx"
  - "上传xls"
  - "batch_id"
  - "批量更新"
  - "批量导入"
  - "更新主数据"
  - "更新产品"
  - "新增产品"
  - "import data"
  - "upload xlsx"
  - "batch update"
---

# Master-data upload — 5-step playbook

> 🚫 **HALLUCINATION GUARDRAIL — read before anything else.**
>
> **The column list and download URL are NOT in this SKILL.md.** They live
> ONLY in the output of the `get_upload_template` tool. This is deliberate —
> if they were duplicated here, you'd be tempted to copy from memory instead
> of calling the tool, and any drift between code and prose would silently
> mislead users.
>
> You DO NOT know the column names from training data. Common-sense guesses
> like `product_name_en`, `product_name_jp`, `currency`, `supplier_code`,
> `category_code`, `country_of_origin_code`, `unit_price` are **wrong** for
> this system. The real names are different and you only learn them by
> calling the tool.
>
> Likewise you DO NOT know the download URL. ANY url you write that you did
> not get from a tool output is a hallucination. URLs like
> `https://example.com/...`, `https://storage.googleapis.com/...`,
> `https://cruise-...vercel.app/...`, etc. are **all wrong**.
>
> **The single safe sequence**: call `get_upload_template()` → paste its
> output verbatim. Never paraphrase or reconstruct from memory.

You are guiding the user through a master-data upload. The 5 steps below are non-negotiable; never skip ahead. Quality matters more than speed: the result of this skill is destructive writes to the master catalog (products, suppliers, …), so a careful audit + explicit user confirmation is mandatory.

## Tools you will use

- `get_upload_template()` — emit the canonical product-upload template (markdown + download link). Call this at Step 0.
- `list_my_uploads(status, limit)` — find existing batches when the user only mentioned "我刚上传的那批"
- `parse_uploaded_file(batch_id)` — parse the Excel + match every row against the DB; returns aggregate stats
- `preview_upload(batch_id, limit)` — get the create/update diff with field-level changes (4-state schema). Use to compute aggregates (Step 3); do NOT paste the output as a table — render via `present_artifact` instead.
- `inspect_upload_row(batch_id, row_index)` — full 4-state state of every field for ONE row; the ONLY truthful way to answer "what's happening with row N specifically"
- `present_artifact(component="upload_diff_viewer", data, narration)` — render the diff as a rich viewer in the user's chat (Step 4). Supports view modes table / cards / heatmap / field_grouped + filter on action. The frontend fetches the actual cell values; agent only supplies `batch_id` + view config.
- `propose_action(action="commit_upload_batch", summary="...", payload='{"batch_id": N}')` — HITL commit (requires user approval in UI). The `batch_id` goes in `payload`; the legacy `target_id` parameter is no longer required (the validator accepts either, but `payload` is the canonical form).
- `propose_action(action="rollback_upload_batch", summary="...", payload='{"batch_id": N}')` — abandon a batch
- `search_masterdata(entity, query, limit)` — verify an entity exists when fuzzy match is uncertain

## Workflow

Copy this checklist into your first reply and tick items off as you progress:

```
Upload Plan (batch_id=___):
- [ ] Step 0 · provide template (only when user has not yet uploaded)
- [ ] Step 1 · classify intent (INSERT / UPDATE / MIXED)
- [ ] Step 2 · confirm target table
- [ ] Step 3 · compute aggregate quality indicators
- [ ] Step 4 · discuss findings with user
- [ ] Step 5 · commit (HITL) / decline / loop
```

### Step 0 · provide template — **HARD RULE, no exceptions**

**Before writing ANY text in your reply**, answer two yes/no questions
about the user's most recent message:

1. Did the user supply a numeric `batch_id`?
2. Is there a file attached in this turn (uploaded via the chat input)?

**If both answers are NO** → your VERY FIRST action MUST be a tool call
to `get_upload_template()`. Do not write narration first. Do not
"acknowledge" the user. Do not ask clarifying questions yet. **CALL THE
TOOL FIRST.**

After the tool returns its markdown body, paste that body **verbatim**
into your reply (it already contains the download link and column
reference). Add at most one short preamble line and one short
postamble line, then STOP. Wait for the user to upload before doing
anything else.

⚠️ **Never tell the user "please download the template" without
attaching the link.** YOU have the tool that produces the link —
saying "please download" without calling the tool leaves the user
with no way to find it (they'll see your message, scroll up, scroll
down, then complain "我没看见链接"). This is the #1 failure mode of
this skill; the worked example below shows the correct shape.

🚫 **Absolutely never invent the download URL or fabricate column
names.** Do NOT write things like `https://example.com/template.xlsx`
or `[下载](placeholder)`. Do NOT guess at columns
(`product_name_en`, `country_of_origin_code`, `category_code` etc.
are NOT real columns of this template). The tool is the single source
of truth for both the URL and the column reference; calling it costs
nothing and is the ONLY safe way to obtain either. If you find
yourself about to type a fake URL, stop and call the tool instead.

**If the user already has a batch_id** → Skip Step 0, go directly to
Step 1. They have already uploaded, sending the template now is noise.

### Step 1 · classify intent

Read the user's last message. Pick exactly one:

- **INSERT** — phrases like "新增", "加进来", "new products", "create".
  → Existing-row matches in Step 3 become *warnings* ("did you mean to overwrite?").
- **UPDATE** — phrases like "更新", "改价格", "refresh prices".
  → No-match rows in Step 3 become *warnings* ("not found, do we create them or skip?").
- **MIXED** — file genuinely contains both, or user is unclear.
  → Both kinds of warnings stay informative, neither is fatal.

If the user's intent is ambiguous from their wording alone, ASK ONE SHORT QUESTION before continuing. Do not proceed with assumptions.

### Step 2 · confirm target table

Call `parse_uploaded_file(batch_id)`. The response includes `entity_type` (today the only supported value is `"products"`).

- If `entity_type` matches what the user described (e.g. user said "更新产品价格" + entity_type=products) → confirm in your reply ("锁定 products 表") and proceed.
- If the user mentioned a different table (`suppliers`, `exchange_rates`, `categories`, …) → say so explicitly and STOP. Those tables don't yet support batch upload; offer the agent's single-record `update_supplier` / `propose_action(create_supplier)` path instead.
- If the parse failed (`status="error"`) → surface `error_message` and stop.

### Step 3 · aggregate quality indicators (NEVER list rows one-by-one)

Call `preview_upload(batch_id)`. From its output PLUS the parse stats, compute exactly these indicators:

| Indicator | Source | Why it matters |
|---|---|---|
| `total_rows` | parse stats | Sanity check |
| `create_count` | preview.summary.create | Will INSERT new rows |
| `update_count` | preview.summary.update | Will UPDATE existing rows |
| `error_count` | preview.summary.error | Parser couldn't read — likely missing required field |
| `duplicate_in_file` | scan preview rows for repeated `code` | File-internal duplication is usually a typo |
| `suspicious_changes` | preview.update[*].fields where price multiplier > 5x OR currency changes OR unit changes | Most common: unit-mistake (KG vs PCS) or decimal slip |

**4-state schema reminder** — every field in `preview.update[*].fields` has shape
`{action, db, excel, will_write}` where `action ∈ {change, unchanged, keep_db, set_new}`.
Only fields with `action ∈ {change, set_new}` will actually be written (also exposed as
`will_write=True` and the convenience `will_write_fields` list on each row).

**Suspicious change detection** — for each update row, only inspect fields where
`action == "change"` (writes that overwrite an existing DB value):
- `price` — compare `price.db` vs `price.excel`; if ratio > 5 or < 0.2, flag it.
- `currency` — if `currency.action == "change"`, flag it.
- `unit` — if `unit.action == "change"`, flag it (the SKU already existed by definition).

DO NOT enumerate every row. Compute counts. DO NOT flag `action == "unchanged"` or
`action == "keep_db"` rows — those write nothing and aren't suspicious.

### Step 4 · render the diff via `present_artifact` + ask for confirmation

**HARD RULE — DO NOT write a markdown table or per-row summary.** We
measured the failure mode in 2026-05-19: at 200 rows the agent silently
truncates 44.5% of rows and ends mid-sentence; at 50 rows multi-turn it
drops 30/50 rows with no warning. Use the artifact channel instead.

**The two-call shape for Step 4:**

1. **Render the viewer** by calling:
   ```
   present_artifact(
     component="upload_diff_viewer",
     data='{"batch_id": <N>, "view": {"mode": "<MODE>", "filter": {...}}}',
     narration="<see template below>"
   )
   ```
   Pick `view.mode` to match the user's request:
   - `"table"` (default) — desktop, "详细看每一行 / all fields"
   - `"cards"` — phone, "我在手机上 / mobile"
   - `"heatmap"` — large batch (100+ rows) overview, "鸟瞰图 / overview"
   - `"field_grouped"` — "哪些字段会改 / by field / 按字段分组"

   Optional `view.filter.action`: subset of `{change, unchanged, keep_db, set_new}`.
   Use when the user says "只看真正会改的" → `["change", "set_new"]`.

2. **In your reply text** (the chat message accompanying the tool call),
   write 1-3 short sentences asking the confirmation questions. Do NOT
   repeat what's in the viewer.

**Narration template** (the `narration` argument to `present_artifact`,
shown as the artifact's caption — keep it grounded in real values):

```
{N} 行更新 / {N} 行新增 / {N} 行无变化。最值得注意的是：{one concrete
observation referencing a real row and value, e.g. 'PROBE-0012 的 currency
从 USD → JPY' or '5 行 price 涨幅 > 5×'}。
```

Two to three sentences max. Generic phrases like `"上传预览"` or `"请查看
详情"` are unacceptable — pick a row whose change is surprising (price >5×,
currency change, supplier change) and name it.

**Confirmation message template** (your chat reply, separate from the
artifact narration):

```
📊 Upload `{filename}` (batch_id={N}) · 类型: {entity_type} · 意图: {intent}

需要你确认:
1. {one specific question — phrase it concretely, quote actual values}
2. {another concrete question — at most 3-4 total}

回复 "OK" / "提交" 走 commit；"取消" 走 cancel（丢弃这次上传）；或者直接告诉我你的纠正。
```

Concrete-question rules:
- Never ask "怎么办" — always ask a binary or list-pick question.
- Quote the actual values where useful: "SKU 'BEEF-X-001' 价格 5.0 → 50.0（10x），单位错了吗？"
- For each warning category, surface 1-2 representative rows in the question, never all of them.
- The aggregate counts (`create / update / error / fuzzy / suspicious`)
  live IN the artifact's view — don't re-state them in chat unless they
  drive a specific question.

### Step 5 · terminal

Three valid endings:

1. **commit** — user said OK / 提交 / 确认.
   Call `propose_action(action="commit_upload_batch", summary="提交批次 #N（...）", payload='{"batch_id": N}')`
   and **immediately stop**. Output one short line like
   "好的，已提交审批 #N，请点击下方卡片的「确认执行」按钮"。
   **Do NOT promise to do anything more** ("等我处理完会告诉你" / "处
   理完成后通知你"). The system automatically triggers a follow-up turn
   right after the user clicks approve, and the model in that follow-up
   turn will tell the user the real result. If you promise here, you'll
   accumulate stale claims in chat.
   **Do NOT respond to messages like "确定" / "我已经确认了"** by
   pretending the action is running — those are just user words; the
   actual trigger is the button on the approval card.

2. **decline** — user said 取消 / 不对 / 撤销 (batch is still at status=resolved, not yet committed).
   Call `propose_action(action="cancel_upload_batch", summary="取消批次 #N", payload='{"batch_id": N}')`.
   Same shape — one short line ("已提交取消审批 #N，请点击「确认执行」"),
   then stop. The follow-up turn covers the post-decision message.

   ⚠️  **Do NOT use `rollback_upload_batch` here.** Rollback is for batches
   that *already wrote rows to `products`* (status=completed). At the HITL
   gate the batch is still at `resolved` — nothing was written — so the
   right verb is *cancel*. Using rollback here will fail with
   `BatchInWrongState`. The distinction matters because rollback walks a
   changelog (expensive, transactional); cancel just marks the batch.

3. **continue** — user has more questions, wants to skip rows, asks "重新读一下" etc.
   Re-run only the affected step (e.g. re-run Step 3 with a filter). Do NOT re-run the whole pipeline. Loop back to Step 4 with updated indicators.

### How the approval follow-up works (so you can predict the flow)

The backend has a **system-driven follow-up turn**: when the user clicks
"确认执行" on the approval card, the dispatch runs server-side and a
new agent turn is automatically triggered with the dispatch result as
its input. That turn's user message starts with `[__APPROVAL_FOLLOWUP__]`
(invisible in the UI). If you find yourself reading a message that
begins with that marker, you are IN the follow-up turn — go to **Step 5b**
below for the structured reflection template (it replaces ad-hoc replies).

### Step 5b · verify + reflect + notify (followup turn ONLY)

You are in followup turn when the latest user message starts with
`[__APPROVAL_FOLLOWUP__]` AND `action` in it is one of
`commit_upload_batch` / `rollback_upload_batch`. Walk these 5 mini-steps
in order:

1. **Parse** the embedded `result` dict. Expected fields:
   `created`, `updated`, `skipped`, `errors`, `error_details` (list).

2. **Classify** the outcome:
   - **✅ Full success** — `errors == 0`
   - **⚠️ Partial success** — `0 < errors < total_attempted`
   - **❌ Full failure** — `errors == total_attempted` OR the result dict
     itself indicates dispatch error (e.g. `{"error": "..."}` only)

3. **Group errors by type** (if `errors > 0`):
   - Take each entry in `error_details`. Extract the exception class
     prefix (`IntegrityError` / `ValueError` / `FK not found` / …) from
     the `errors[0]` string.
   - Bucket by prefix; keep counts.
   - Pick the top 3 buckets. For each, capture ONE representative row
     (`row_index` + `product_code`) for the user to verify.

4. **Compose reply** per template (in user's language, Chinese by default):

   - Full success:
     ```
     ✅ 批次 #{batch_id} 处理完成
     · 创建 {created}，更新 {updated}，跳过 {skipped}
     ```

   - Partial success:
     ```
     ⚠️ 批次 #{batch_id} 部分成功
     · 成功：创建 {created} / 更新 {updated} / 跳过 {skipped}
     · 失败：{errors} 行
     · 失败原因（前 3 类）：
       · {type1}: {count1} 行（例：row {row_idx} {code}）
       · {type2}: {count2} 行
       · {type3}: {count3} 行
     · 建议：{one concrete next-step}
     ```

   - Full failure:
     ```
     ❌ 批次 #{batch_id} 失败
     · {errors} 行全部出错
     · 主要原因: {top_type}
     · 建议: {action user should take}
     ```

5. **Concrete suggestion library** — match the top error type to a fix:

   | 错误类型 / 关键词 | 建议 |
   |---|---|
   | `supplier` not found / `category` not found / `country` not found / `port` not found | 请先到 `/dashboard/data` 对应 tab 添加这些主数据，再重新上传 |
   | `IntegrityError` ... `uix_country_product_name_port` | DB 中已存在同 (国家, 名字, 港口) 的产品，请检查 Excel 是否有重复行，或确认你想做的是 UPDATE 还是 INSERT |
   | 有效日期字段 ... not a valid date | 检查产品、采购价或卖价的日期列，必须 YYYY-MM-DD 格式（如 2026-05-30） |
   | 采购价/卖价有效开始日期不能晚于结束日期 | 调整对应价格的开始或结束日期后重新上传 |
   | `missing product_name` | Excel 有空 product_name 行，删掉后重传 |
   | other | 调 `preview_upload(batch_id={batch_id})` 看每行 validation_errors 字段 |

**Rules for Step 5b**:
- You MAY call `preview_upload(batch_id)` once if the result lacks
  `error_details` or contains "..." truncation — but DO NOT call
  `propose_action` again (that would start a new HITL loop).
- DO NOT paste the raw JSON. DO NOT enumerate every failed row.
- DO use the structured template — it's deterministic and testable.

### Step 5c · row-specific drill-down — **HARD RULE, no exceptions**

This step covers any moment — Step 4 sanity check, Step 5b error
follow-up, or a free-form question after commit — when the user asks
about ONE specific row, ONE specific product, or ONE specific field's
fate. Examples that trigger this rule:

- "第 5 行的 pack_size 怎么改的？"
- "ROBA-001 这个产品的 currency 我改了没？"
- "BEEF-X-001 的 price 为什么没动？"
- "刚才那个 row=12 你说要 skip，确认一下它的 unit 是什么状态？"

**The rules**:

1. ❌ **绝对禁止**从已经看过的 `preview_upload` 输出里"想起"具体值或字段
   状态再回答。`preview_upload` 默认每组截断到 50 行，且为聚合视图；
   你脑子里那份"我记得 row 5 的 pack_size 是 X"基本一定是幻觉。
2. ❌ **绝对禁止**用 "Excel 里应该没填" / "估计是..." / "我记得是..." /
   "因为没出现在 diff 里所以..." 这类推测语。`preview` 不出现的字段
   不等于"Excel 没填"——可能是 `unchanged`，可能是 `keep_db`，可能
   是 truncation，三种 root cause 都常见。
3. ✅ **必须**先调 `inspect_upload_row(batch_id=N, row_index=M)`，
   然后从它返回的 `fields.<name>.action` + `db` + `excel` + `will_write`
   读真实状态，再回答用户。
4. ✅ 回答里 **引用真实值**，不要只说 action 名：
   ❌ "row 5 的 pack_size 是 unchanged"
   ✅ "row 5 的 pack_size，DB 里是 `40LB/CT`，Excel 也写了 `40LB/CT`，
   完全一样所以本次提交不会动这个字段（action=unchanged）。"
5. ✅ 如果 `inspect_upload_row` 报 `row_index not found` —— 如实说
   "这个 batch 没有第 N 行"，不要猜 "可能是第 N-1 行" 之类。

**Why this is a HARD rule, not a soft suggestion**: 2026-05-19 prod 事故
就是 agent 在 preview 的字段里只看到 4 个 changed 字段（旧 diff
schema 只 emit changed 字段），就告诉用户 "Excel 里没填 pack_size"——
其实 Excel 写了同样的值（unchanged），agent 凭空编出来。新的 `inspect_upload_row`
返回全部 mutable 字段（当前为 19 个）每一个的真实状态，把这种幻觉从根上堵死。

## Anti-patterns

- ❌ Listing rows one-by-one in your message ("Row 1 OK, Row 2 OK…"). Aggregate.
- ❌ Calling `commit_upload_batch` before the user explicitly confirms.
- ❌ Inventing values for missing required fields. Ask the user.
- ❌ Proceeding past Step 1 when the intent is genuinely ambiguous.
- ❌ Treating "fuzzy match" as silent OK — always surface count and a sample for confirmation.
- ❌ Replacing this 5-step playbook with a "I'll just do it" shortcut. The audit trail matters.
- ❌ Answering row-specific questions ("第 N 行的 pack_size") from memory of an earlier preview output. Always call `inspect_upload_row(batch_id, row_index)` — see Step 5c HARD RULE.
- ❌ Inferring "Excel 里没填 X" because field X wasn't in the diff. Diff omitted fields may be `unchanged`, `keep_db`, OR `set_new` — only `inspect_upload_row` tells you which.
- ❌ Pasting the preview as a markdown table in Step 4. We measured: at 200 rows agent silently truncates 44.5% mid-sentence. Always call `present_artifact(component="upload_diff_viewer", ...)`.
- ❌ Generic narration like "上传预览" or "请查看详情". Use the Step 4 narration template — quote a real row + value where the change is surprising.
- ❌ **NEVER suggest the user rename their product code or product name as a workaround for matching ambiguity.** Codes and names are stable business identifiers — they come from external systems (supplier catalogs, ERP). Suffix-style hacks like `99PRD010725-TK` or appending `(Tokyo)` to a name silently fork the canonical SKU and break every downstream report. When the user uploads to a new port and `preview_upload` shows surprising UPDATE counts, the correct guidance is:

    > "想在新港口为同一个产品建独立记录？请在 Excel 加 `port` 列（值填新港口名称），系统会把它当作新行插入，原港口数据不动。"

  If they object that they already have 117 rows to edit, offer to **cancel the current batch** and walk through fixing the Excel — never offer to commit-as-rename. (Same-code-different-port = different rows is the schema's design, not a limitation.)

## Worked examples

### Example 1 — user has NOT yet uploaded (most common entry point)

User: "我想上传一份 Excel 来批量更新产品数据"

Analysis: no `batch_id` mentioned, no file attached → Step 0 applies.

**Your immediate action**: call `get_upload_template()` BEFORE writing
any reply text.

After the tool returns its markdown body, your reply looks like:

```
好的，先给你模板，填完后直接拖进对话框：

📋 **产品上传模板**

[📥 下载模板（product_upload_template.xlsx）](…link…)

**模板说明** —— 工作表「产品数据」共 5 列：
| 列名 | 必填? | … |
| --- | --- | --- |
…
（the rest of the tool output, pasted verbatim）

Upload Plan (batch_id=___):
- [x] Step 0 · 模板已发送
- [ ] Step 1 · 等用户填好上传
- [ ] Step 2-5 · ...

填好后拖进对话框，我接管后续步骤。
```

DO NOT in this case skip the tool call and write only "请下载模板" —
the user won't have a link.

### Example 2 — user already uploaded

User: "我刚传了 batch_id=12，把这些产品价格更新一下"

Analysis: `batch_id=12` present → Skip Step 0, go to Step 1.

Your first reply:
```
好的，我来按 5 步走。先把 plan 列出来：

Upload Plan (batch_id=12):
- [-] Step 0 · 跳过（已有 batch_id）
- [x] Step 1 · 意图: UPDATE（用户说"更新价格"）
- [ ] Step 2 · 确认表
- [ ] Step 3 · 质量指标
- [ ] Step 4 · 沟通
- [ ] Step 5 · commit / decline

调 parse_uploaded_file 看一下…
```

Then call `parse_uploaded_file(batch_id=12)`, then `preview_upload(batch_id=12)`, then aggregate and emit the Step 4 message.
