"use client";

/**
 * Typed tool-call UIs for the /dashboard/agent chat workspace.
 *
 * Why a registry: the v3 agent has ~25 tools. Rendering each as a JSON
 * dump is hostile to non-technical users. But hand-crafting 25 unique
 * React components is over-engineering — most tools fall into 5 shapes:
 *
 *   1. query  — returns a list/rows  (query_db, search_*, list_*)
 *   2. detail — returns one object   (get_*, read_*, inspect_*)
 *   3. action — returns a status     (parse_*, preview_*, update_*, generate_*)
 *   4. memory — returns one ack      (remember, recall, load_skill)
 *   5. artifact — handoff to right pane (present_artifact)
 *
 * Each tool is registered in `TOOL_CONFIGS` with its category + label +
 * optional `describe(args)` formatter. `buildToolUIs()` returns the
 * `Record<string, ToolCallMessagePartComponent>` that
 * `MessagePrimitive.Parts` consumes via `components.tools.by_name`.
 *
 * The Fallback handles anything not registered (e.g. an agent that adds
 * a new tool before this file catches up).
 *
 * What stays out of this file:
 *   - Backend tool *implementations* (those live in v3_backend/agent/runtime/tools/).
 *   - Markdown rendering (results are data, not prose — `<pre>` only).
 *   - Right-pane artifact rendering (the ArtifactPane component owns that).
 */

import { useState } from "react";
import type {
  ToolCallMessagePartComponent,
  ToolCallMessagePartProps,
} from "@assistant-ui/react";
import { ArrowRight, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";

import { useArtifactSelector } from "@/components/assistant/ArtifactSelectorContext";

// ─── Pure helpers (exported for unit tests) ──────────────────

/**
 * Truncate a string to `max` chars, appending "…" if cut.
 * Used to keep tool labels and inline values from blowing out the layout
 * during streaming when args may grow large.
 */
export function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return s.slice(0, Math.max(0, max - 1)) + "…";
}

/**
 * Attempt to coerce a backend tool result into a row list.
 *
 * Inputs we see in practice:
 *   - `undefined` / `null`            → not finished yet → null
 *   - `string`                        → may be JSON; try parse, otherwise null
 *   - `Array<object>`                 → already a row list
 *   - `{ items: [...] }` / `{ rows: [...] }` / `{ data: [...] }`
 *                                     → unwrap and treat as a row list
 *   - `{ ok: true, count: 0, ... }`   → status object, no rows
 *
 * Returns `null` when nothing usable can be extracted — callers fall
 * back to showing the raw text + "已完成".
 */
export function parseRowsFromResult(
  result: unknown,
): Array<Record<string, unknown>> | null {
  if (result == null) return null;

  let parsed: unknown = result;
  if (typeof result === "string") {
    const trimmed = result.trim();
    if (!trimmed) return null;
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      return null;
    }
  }

  if (Array.isArray(parsed)) {
    return parsed.filter(
      (row): row is Record<string, unknown> =>
        row !== null && typeof row === "object" && !Array.isArray(row),
    );
  }

  if (parsed && typeof parsed === "object") {
    for (const key of ["items", "rows", "data", "results"] as const) {
      const candidate = (parsed as Record<string, unknown>)[key];
      if (Array.isArray(candidate)) {
        return candidate.filter(
          (row): row is Record<string, unknown> =>
            row !== null && typeof row === "object" && !Array.isArray(row),
        );
      }
    }
  }

  return null;
}

/**
 * Infer a small column set for a MiniTable from a list of row objects.
 *
 * Goals:
 *   - Show up to `maxCols` columns.
 *   - Prefer "id" / "name" / "code" first (most identifying fields).
 *   - Skip nested objects/arrays — they don't fit in a 11px cell.
 *
 * Returns the chosen column keys in display order.
 */
export function inferColumns(
  rows: Array<Record<string, unknown>>,
  maxCols = 5,
): string[] {
  if (rows.length === 0) return [];
  const scalarKeys = new Set<string>();
  for (const row of rows) {
    for (const [k, v] of Object.entries(row)) {
      if (
        v === null ||
        typeof v === "string" ||
        typeof v === "number" ||
        typeof v === "boolean"
      ) {
        scalarKeys.add(k);
      }
    }
  }
  const all = Array.from(scalarKeys);
  // Priority sort: identifying fields first, then alphabetical.
  const priority = ["id", "name", "code", "title", "label", "type", "status"];
  all.sort((a, b) => {
    const ai = priority.indexOf(a);
    const bi = priority.indexOf(b);
    if (ai !== -1 && bi !== -1) return ai - bi;
    if (ai !== -1) return -1;
    if (bi !== -1) return 1;
    return a.localeCompare(b);
  });
  return all.slice(0, maxCols);
}

/**
 * Build a human-readable "doing X..." label from a tool's args object.
 *
 * Returns the bare tool label when no args make sense to surface (and
 * the caller falls through to the registry label alone).
 */
export function describeArgs(
  toolName: string,
  args: unknown,
): string | null {
  if (args === undefined || args === null) return null;
  if (typeof args !== "object") return null;
  const a = args as Record<string, unknown>;

  switch (toolName) {
    case "query_db": {
      const sql = typeof a.sql === "string" ? a.sql : null;
      if (!sql) return null;
      // Strip leading whitespace + newlines so the label is one line.
      return truncate(sql.replace(/\s+/g, " ").trim(), 60);
    }
    case "search_masterdata":
    case "search_documents":
    case "web_search": {
      const q = typeof a.query === "string" ? a.query : null;
      if (!q) return null;
      return truncate(q, 40);
    }
    case "get_order_detail":
    case "update_order":
    case "rematch_order": {
      const id = a.order_id;
      return id != null ? `订单 #${id}` : null;
    }
    case "get_document":
    case "read_document_section": {
      const id = a.document_id;
      return id != null ? `文档 #${id}` : null;
    }
    case "update_product": {
      const id = a.product_id;
      return id != null ? `产品 #${id}` : null;
    }
    case "update_supplier": {
      const id = a.supplier_id;
      return id != null ? `供应商 #${id}` : null;
    }
    case "inspect_upload_row": {
      const batch = a.batch_id;
      const row = a.row_index;
      if (batch != null && row != null) return `批次 #${batch} 第 ${row} 行`;
      if (batch != null) return `批次 #${batch}`;
      return null;
    }
    case "preview_upload":
    case "parse_uploaded_file": {
      const batch = a.batch_id;
      return batch != null ? `批次 #${batch}` : null;
    }
    case "generate_inquiry":
    case "regenerate_supplier_inquiry": {
      const id = a.order_id;
      return id != null ? `订单 #${id}` : null;
    }
    case "web_fetch": {
      const url = typeof a.url === "string" ? a.url : null;
      if (!url) return null;
      try {
        return new URL(url).host;
      } catch {
        return truncate(url, 40);
      }
    }
    case "remember":
    case "recall": {
      const key =
        typeof a.key === "string"
          ? a.key
          : typeof a.query === "string"
            ? a.query
            : null;
      return key ? truncate(key, 40) : null;
    }
    case "load_skill": {
      const name = typeof a.name === "string" ? a.name : null;
      return name ? truncate(name, 40) : null;
    }
    case "present_artifact": {
      const narration =
        typeof a.narration === "string" ? a.narration : null;
      return narration ? truncate(narration, 60) : null;
    }
    default:
      return null;
  }
}

// ─── Tool registry ──────────────────────────────────────────

type ToolCategory =
  | "query"
  | "detail"
  | "action"
  | "memory"
  | "artifact";

interface ToolUIConfig {
  category: ToolCategory;
  label: string;
}

/**
 * Registry — adding a new backend tool = one row here.
 * Unlisted tools render via the Fallback.
 */
export const TOOL_CONFIGS: Record<string, ToolUIConfig> = {
  // ─ query (rows / lists) ─
  query_db: { category: "query", label: "查询数据库" },
  search_masterdata: { category: "query", label: "搜索主数据" },
  list_orders: { category: "query", label: "列出订单" },
  list_documents: { category: "query", label: "列出文档" },
  list_my_uploads: { category: "query", label: "我的上传" },
  search_documents: { category: "query", label: "搜索文档" },
  web_search: { category: "query", label: "网络搜索" },
  // ─ detail (single object) ─
  get_order_detail: { category: "detail", label: "查看订单详情" },
  get_document: { category: "detail", label: "查看文档" },
  read_document_section: { category: "detail", label: "读取文档片段" },
  inspect_upload_row: { category: "detail", label: "检查上传行" },
  get_upload_template: { category: "detail", label: "获取上传模板" },
  // ─ action (status / effect) ─
  parse_uploaded_file: { category: "action", label: "解析文件" },
  preview_upload: { category: "action", label: "预览上传变更" },
  update_order: { category: "action", label: "更新订单" },
  update_product: { category: "action", label: "更新产品" },
  update_supplier: { category: "action", label: "更新供应商" },
  rematch_order: { category: "action", label: "重新匹配订单" },
  generate_inquiry: { category: "action", label: "生成询价单" },
  regenerate_supplier_inquiry: {
    category: "action",
    label: "重生成供应商询价",
  },
  web_fetch: { category: "action", label: "读取网页" },
  // ─ memory (acks) ─
  load_skill: { category: "memory", label: "加载技能" },
  remember: { category: "memory", label: "记忆" },
  recall: { category: "memory", label: "调取记忆" },
  // ─ artifact (right-pane handoff) ─
  present_artifact: { category: "artifact", label: "工作面板" },
};

// ─── Shared sub-components ──────────────────────────────────

/**
 * The single-line "doing X..." or "did X (✓)" row used by every category
 * for the pending/collapsed state.
 */
function ToolRow({
  label,
  detail,
  status,
  open,
  onToggle,
  expandable = true,
}: {
  label: string;
  detail?: string | null;
  status: "running" | "done" | "error";
  open: boolean;
  onToggle: () => void;
  expandable?: boolean;
}) {
  const Icon = status === "running" ? Loader2 : null;
  return (
    <button
      type="button"
      onClick={expandable ? onToggle : undefined}
      disabled={!expandable}
      className={cn(
        "flex w-full items-center gap-2 rounded-md border px-3 py-1.5 text-left text-xs",
        status === "error"
          ? "border-destructive/40 bg-destructive/5"
          : "border-border bg-background",
        expandable && "hover:bg-muted",
      )}
    >
      {Icon ? (
        <Icon className="h-3 w-3 shrink-0 animate-spin text-muted-foreground" />
      ) : (
        <span
          className={cn(
            "h-1.5 w-1.5 shrink-0 rounded-full",
            status === "done" ? "bg-foreground" : "bg-destructive",
          )}
          aria-hidden
        />
      )}
      <span className="font-medium">{label}</span>
      {detail ? (
        <span className="truncate text-muted-foreground">· {detail}</span>
      ) : null}
      {expandable && (
        <span className="ml-auto text-[10px] text-muted-foreground">
          {open ? "▾" : "▸"}
        </span>
      )}
    </button>
  );
}

/**
 * Mini-table for the first N rows of a query result. Columns auto-inferred.
 */
function MiniTable({
  rows,
  maxRows = 5,
}: {
  rows: Array<Record<string, unknown>>;
  maxRows?: number;
}) {
  const cols = inferColumns(rows);
  const visible = rows.slice(0, maxRows);
  if (cols.length === 0 || visible.length === 0) {
    return (
      <pre className="overflow-x-auto rounded bg-muted px-2 py-1 text-[11px] text-muted-foreground">
        {JSON.stringify(rows, null, 2)}
      </pre>
    );
  }
  return (
    <div className="overflow-x-auto rounded border bg-background">
      <table className="w-full text-[11px]">
        <thead className="bg-muted/50 text-muted-foreground">
          <tr>
            {cols.map((c) => (
              <th key={c} className="px-2 py-1 text-left font-medium">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {visible.map((row, i) => (
            <tr key={i} className="border-t">
              {cols.map((c) => {
                const v = row[c];
                const text =
                  v === null || v === undefined ? "—" : String(v);
                return (
                  <td
                    key={c}
                    className="px-2 py-1 align-top"
                    title={text}
                  >
                    {truncate(text, 80)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > maxRows && (
        <div className="border-t bg-muted/30 px-2 py-1 text-[10px] text-muted-foreground">
          仅显示前 {maxRows} 行 · 共 {rows.length} 行
        </div>
      )}
    </div>
  );
}

/**
 * Common state container for every category. Captures pending/done/error
 * detection and the expand/collapse toggle.
 */
function useToolUIState({ status }: { status: ToolCallMessagePartProps["status"] }) {
  const isRunning = status?.type === "running";
  const isError = status?.type === "incomplete";
  const phase: "running" | "done" | "error" = isRunning
    ? "running"
    : isError
      ? "error"
      : "done";
  const [open, setOpen] = useState(false);
  return { phase, open, setOpen, isError };
}

// ─── Categorical UIs ────────────────────────────────────────

/** Build category-specific component closures around a label + tool name. */
function makeQueryToolUI(label: string, toolName: string): ToolCallMessagePartComponent {
  return function QueryToolUI({ args, result, status }) {
    const { phase, open, setOpen } = useToolUIState({ status });
    const detail = describeArgs(toolName, args);
    const headerLabel =
      phase === "running" ? `正在${label}...` : label;
    const rows = parseRowsFromResult(result);
    return (
      <div className="space-y-1.5">
        <ToolRow
          label={headerLabel}
          detail={detail}
          status={phase}
          open={open}
          onToggle={() => setOpen((v) => !v)}
          expandable={phase !== "running"}
        />
        {open && phase !== "running" && (
          <div className="pl-3">
            {rows && rows.length > 0 ? (
              <MiniTable rows={rows} />
            ) : (
              <RawResult result={result} />
            )}
          </div>
        )}
      </div>
    );
  };
}

function makeDetailToolUI(label: string, toolName: string): ToolCallMessagePartComponent {
  return function DetailToolUI({ args, result, status }) {
    const { phase, open, setOpen } = useToolUIState({ status });
    const detail = describeArgs(toolName, args);
    const headerLabel =
      phase === "running" ? `正在${label}...` : label;
    return (
      <div className="space-y-1.5">
        <ToolRow
          label={headerLabel}
          detail={detail}
          status={phase}
          open={open}
          onToggle={() => setOpen((v) => !v)}
          expandable={phase !== "running"}
        />
        {open && phase !== "running" && (
          <div className="pl-3">
            <RawResult result={result} />
          </div>
        )}
      </div>
    );
  };
}

function makeActionToolUI(label: string, toolName: string): ToolCallMessagePartComponent {
  return function ActionToolUI({ args, result, status }) {
    const { phase, open, setOpen, isError } = useToolUIState({ status });
    const detail = describeArgs(toolName, args);
    const headerLabel =
      phase === "running"
        ? `正在${label}...`
        : isError
          ? `${label}（失败）`
          : `${label} ✓`;
    return (
      <div className="space-y-1.5">
        <ToolRow
          label={headerLabel}
          detail={detail}
          status={phase}
          open={open}
          onToggle={() => setOpen((v) => !v)}
          expandable={phase !== "running"}
        />
        {open && phase !== "running" && (
          <div className="pl-3">
            <RawResult result={result} />
          </div>
        )}
      </div>
    );
  };
}

function makeMemoryToolUI(label: string, toolName: string): ToolCallMessagePartComponent {
  return function MemoryToolUI({ args, status }) {
    const { phase } = useToolUIState({ status });
    const detail = describeArgs(toolName, args);
    const headerLabel = phase === "running" ? `${label}...` : `${label} ✓`;
    return (
      <ToolRow
        label={headerLabel}
        detail={detail}
        status={phase}
        open={false}
        onToggle={() => {}}
        expandable={false}
      />
    );
  };
}

function makeArtifactToolUI(label: string, toolName: string): ToolCallMessagePartComponent {
  return function ArtifactToolUI({ args, status }) {
    const { phase } = useToolUIState({ status });
    const selector = useArtifactSelector();
    const narration = describeArgs(toolName, args);

    if (phase === "running") {
      // Mid-stream — the artifact isn't in the right pane yet, so this
      // is a status row, not a chip. Reuse ToolRow for consistency.
      return (
        <ToolRow
          label={`准备${label}...`}
          detail={null}
          status="running"
          open={false}
          onToggle={() => {}}
          expandable={false}
        />
      );
    }

    // Done. Render a clickable chip that jumps the right pane to the
    // matching artifact. `selectByNarration` looks it up against the
    // live artifacts list held in the provider.
    const canJump =
      !!narration && (selector?.hasArtifactWithNarration(narration) ?? false);

    const onClick = () => {
      if (!narration || !selector) return;
      selector.selectByNarration(narration);
    };

    return (
      <button
        type="button"
        onClick={onClick}
        disabled={!canJump}
        className={cn(
          "group flex w-full items-center gap-2 rounded-md border px-3 py-1.5 text-left text-xs transition-colors",
          canJump
            ? "border-border bg-background hover:border-foreground/40 hover:bg-muted"
            : "border-border bg-background opacity-70",
        )}
        title={canJump ? "跳到工作面板" : narration || label}
      >
        <span
          className={cn(
            "h-1.5 w-1.5 shrink-0 rounded-full",
            canJump ? "bg-foreground" : "bg-muted-foreground",
          )}
          aria-hidden
        />
        <span className="font-medium">已生成：</span>
        <span className="truncate text-muted-foreground">
          {narration || label}
        </span>
        {canJump && (
          <ArrowRight className="ml-auto h-3 w-3 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
        )}
      </button>
    );
  };
}

// ─── Fallback (unknown tool) ────────────────────────────────

/**
 * For any tool not in TOOL_CONFIGS. Used to be the page's `ToolCallPart`.
 * Shows args + result as raw JSON — degrades gracefully when a new
 * backend tool ships before this file knows about it.
 */
export const FallbackToolUI: ToolCallMessagePartComponent = ({
  toolName,
  args,
  result,
  status,
}) => {
  const { phase, open, setOpen } = useToolUIState({ status });
  const argsStr =
    typeof args === "object" && args !== null
      ? JSON.stringify(args, null, 2)
      : String(args ?? "");
  return (
    <div className="space-y-1.5">
      <ToolRow
        label={
          phase === "running"
            ? `正在调用工具 · ${toolName}...`
            : `工具调用 · ${toolName} ✓`
        }
        detail={null}
        status={phase}
        open={open}
        onToggle={() => setOpen((v) => !v)}
        expandable={phase !== "running"}
      />
      {open && (
        <div className="space-y-1.5 pl-3">
          <div>
            <div className="text-[10px] text-muted-foreground">args</div>
            <pre className="overflow-x-auto rounded bg-muted px-2 py-1 text-[11px]">
              {argsStr || "{}"}
            </pre>
          </div>
          <RawResult result={result} />
        </div>
      )}
    </div>
  );
};

function RawResult({ result }: { result: unknown }) {
  if (result === undefined || result === null) return null;
  const text =
    typeof result === "object"
      ? JSON.stringify(result, null, 2)
      : String(result);
  if (!text) return null;
  return (
    <div>
      <div className="text-[10px] text-muted-foreground">result</div>
      <pre className="max-h-48 overflow-auto rounded bg-muted px-2 py-1 text-[11px]">
        {text}
      </pre>
    </div>
  );
}

// ─── Public assembly ────────────────────────────────────────

/**
 * Returns the registry to pass to `MessagePrimitive.Parts`'s
 * `components.tools.by_name`. Pure: same call always yields the same
 * component identities, so React doesn't churn.
 */
let _cached: Record<string, ToolCallMessagePartComponent> | null = null;
export function buildToolUIs(): Record<string, ToolCallMessagePartComponent> {
  if (_cached) return _cached;
  const out: Record<string, ToolCallMessagePartComponent> = {};
  for (const [name, cfg] of Object.entries(TOOL_CONFIGS)) {
    switch (cfg.category) {
      case "query":
        out[name] = makeQueryToolUI(cfg.label, name);
        break;
      case "detail":
        out[name] = makeDetailToolUI(cfg.label, name);
        break;
      case "action":
        out[name] = makeActionToolUI(cfg.label, name);
        break;
      case "memory":
        out[name] = makeMemoryToolUI(cfg.label, name);
        break;
      case "artifact":
        out[name] = makeArtifactToolUI(cfg.label, name);
        break;
    }
  }
  _cached = out;
  return out;
}
