"use client";

/**
 * UploadDiffViewer — declarative artifact renderer for master-data
 * upload preview (4-state diff).
 *
 * Triggered by the v3 backend's `artifact` SSE event when the agent
 * calls `present_artifact(component="upload_diff_viewer", ...)`. The
 * agent supplies a tiny reference payload + view config:
 *
 *   data = { batch_id: 12, view?: { mode, filter, group_by } }
 *
 * This component fetches the actual 4-state preview JSON from
 * GET /api/artifacts/upload-diff/{batch_id} — leaf values NEVER come
 * from the LLM. See docs/current_progress/2026-05-19/agent-html-
 * interaction-architecture.md for the architecture rationale + the
 * measurement data that drove the design (200-row HTML rendering
 * silently truncated 44.5% of rows; this design eliminates that path).
 *
 * View modes:
 *   - table          row × field grid (the default — best on desktop)
 *   - cards          one expandable card per row (mobile-friendly)
 *   - heatmap        row × field colored chips, no values until hover
 *                    (best for 100+ rows)
 *   - field_grouped  pivots: section per field listing affected rows
 */

import { useEffect, useState } from "react";
import {
  getUploadDiffArtifact,
  type UploadDiffArtifactPayload,
  type UploadDiffFieldState,
  type UploadDiffRow,
} from "@/lib/v3-chat-api";

type ViewMode = "table" | "cards" | "heatmap" | "field_grouped";
type ActionFilter = "change" | "unchanged" | "keep_db" | "set_new";

interface ViewConfig {
  mode?: ViewMode;
  filter?: { action?: ActionFilter[] };
  group_by?: "row" | "field" | "action";
  highlight?: { row_index?: number };
}

interface Props {
  batchId: number;
  view?: ViewConfig;
}

const ACTION_STYLE: Record<string, { bg: string; label: string; symbol: string }> = {
  change: { bg: "bg-blue-100 text-blue-900 dark:bg-blue-900/40 dark:text-blue-200", label: "改", symbol: "~" },
  set_new: { bg: "bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-200", label: "新", symbol: "+" },
  unchanged: { bg: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400", label: "同", symbol: "=" },
  keep_db: { bg: "bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200", label: "留", symbol: "↩" },
  immutable: { bg: "bg-zinc-50 text-zinc-500 dark:bg-zinc-800/60 dark:text-zinc-500", label: "锁", symbol: "□" },
  error: { bg: "bg-rose-100 text-rose-900 dark:bg-rose-900/40 dark:text-rose-200", label: "错", symbol: "×" },
};

function ActionChip({ action }: { action: string }) {
  const style = ACTION_STYLE[action] ?? ACTION_STYLE.unchanged;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium ${style.bg}`}
      title={action}
    >
      <span className="font-mono">{style.symbol}</span>
      {style.label}
    </span>
  );
}

function renderValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

function applyFilter(
  rows: UploadDiffRow[],
  filter: { action?: ActionFilter[] } | undefined,
): UploadDiffRow[] {
  if (!filter?.action || filter.action.length === 0) return rows;
  const wanted = new Set<string>(filter.action);
  // Keep a row if ANY of its fields has an action we care about.
  return rows.filter((r) =>
    Object.values(r.fields).some((f) => wanted.has(f.action)),
  );
}

// ─── view: TABLE ────────────────────────────────────────────

function TableView({ rows, highlightRowIndex }: { rows: UploadDiffRow[]; highlightRowIndex?: number }) {
  if (rows.length === 0) {
    return <p className="text-xs text-muted-foreground p-3">没有匹配的行。</p>;
  }
  const fieldKeys = Object.keys(rows[0].fields);
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-xs">
        <thead className="bg-zinc-50 dark:bg-zinc-900 text-left">
          <tr>
            <th className="px-2 py-1.5 font-medium">行</th>
            <th className="px-2 py-1.5 font-medium">code / name</th>
            {fieldKeys.map((k) => (
              <th key={k} className="px-2 py-1.5 font-medium whitespace-nowrap">
                {k}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const highlighted = highlightRowIndex === r.row_index;
            return (
              <tr
                key={r.row_index}
                className={
                  highlighted
                    ? "bg-yellow-50 dark:bg-yellow-900/20 border-l-2 border-yellow-400"
                    : "even:bg-zinc-50/50 dark:even:bg-zinc-900/20"
                }
              >
                <td className="px-2 py-1.5 font-mono text-muted-foreground">{r.row_index}</td>
                <td className="px-2 py-1.5">
                  <div className="font-mono text-[11px]">{r.product_code || "—"}</div>
                  <div className="text-muted-foreground">{r.product_name || ""}</div>
                </td>
                {fieldKeys.map((k) => {
                  const f = r.fields[k];
                  return (
                    <td key={k} className="px-2 py-1.5">
                      <div className="flex items-center gap-1.5">
                        <ActionChip action={f.action} />
                        <span className="text-[11px] font-mono text-muted-foreground">
                          {f.will_write ? renderValue(f.excel) : renderValue(f.db)}
                        </span>
                      </div>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ─── view: CARDS ────────────────────────────────────────────

function CardsView({ rows, highlightRowIndex }: { rows: UploadDiffRow[]; highlightRowIndex?: number }) {
  if (rows.length === 0) return <p className="text-xs text-muted-foreground p-3">没有匹配的行。</p>;
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-2 p-2">
      {rows.map((r) => {
        const highlighted = highlightRowIndex === r.row_index;
        return (
          <details
            key={r.row_index}
            className={`rounded-md border bg-card p-2 text-xs ${
              highlighted ? "ring-2 ring-yellow-400" : "border-border/60"
            }`}
          >
            <summary className="cursor-pointer flex items-center justify-between">
              <div>
                <span className="text-muted-foreground mr-2">行 {r.row_index}</span>
                <span className="font-mono">{r.product_code || "—"}</span>
                <span className="text-muted-foreground ml-1">· {r.product_name}</span>
              </div>
              <span className="text-muted-foreground">
                {r.will_write_fields.length} 字段会写
              </span>
            </summary>
            <ul className="mt-2 space-y-1">
              {Object.entries(r.fields).map(([k, f]) => (
                <li key={k} className="flex items-start gap-2">
                  <span className="w-24 text-muted-foreground shrink-0">{k}</span>
                  <ActionChip action={f.action} />
                  <span className="font-mono text-[11px]">
                    {f.action === "change"
                      ? `${renderValue(f.db)} → ${renderValue(f.excel)}`
                      : f.action === "set_new"
                      ? `(空) → ${renderValue(f.excel)}`
                      : f.action === "keep_db"
                      ? renderValue(f.db)
                      : renderValue(f.db)}
                  </span>
                </li>
              ))}
            </ul>
          </details>
        );
      })}
    </div>
  );
}

// ─── view: HEATMAP ──────────────────────────────────────────

function HeatmapView({ rows }: { rows: UploadDiffRow[] }) {
  if (rows.length === 0) return <p className="text-xs text-muted-foreground p-3">没有匹配的行。</p>;
  const fieldKeys = Object.keys(rows[0].fields);
  return (
    <div className="overflow-x-auto">
      <table className="border-collapse">
        <thead>
          <tr>
            <th className="px-2 py-1 text-xs text-left text-muted-foreground sticky left-0 bg-background">
              行
            </th>
            {fieldKeys.map((k) => (
              <th
                key={k}
                className="px-1 py-1 text-[10px] text-muted-foreground rotate-[-45deg] origin-bottom-left whitespace-nowrap"
                style={{ height: 56 }}
              >
                {k}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.row_index}>
              <td className="px-2 text-[11px] font-mono text-muted-foreground sticky left-0 bg-background">
                {r.row_index}
                <span className="ml-1 text-[10px]">{r.product_code || ""}</span>
              </td>
              {fieldKeys.map((k) => {
                const f = r.fields[k];
                const style = ACTION_STYLE[f.action] ?? ACTION_STYLE.unchanged;
                const tooltip = `${k}: ${f.action}\n  db: ${renderValue(f.db)}\n  excel: ${renderValue(f.excel)}`;
                return (
                  <td key={k} className="p-px">
                    <div
                      className={`w-5 h-5 rounded-sm ${style.bg} flex items-center justify-center text-[9px] font-mono`}
                      title={tooltip}
                    >
                      {style.symbol}
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── view: FIELD_GROUPED ────────────────────────────────────

function FieldGroupedView({ rows }: { rows: UploadDiffRow[] }) {
  if (rows.length === 0) return <p className="text-xs text-muted-foreground p-3">没有匹配的行。</p>;
  // Pivot: field name → list of (row, fieldState).
  const fieldKeys = Object.keys(rows[0].fields);
  const byField = fieldKeys.map((k) => {
    const entries = rows
      .map((r) => ({ row: r, f: r.fields[k] }))
      .filter(({ f }) => f.action === "change" || f.action === "set_new");
    return { field: k, entries };
  });
  const interesting = byField.filter((g) => g.entries.length > 0);
  if (interesting.length === 0) {
    return (
      <p className="text-xs text-muted-foreground p-3">
        没有任何字段被写入。所有 update 行的所有字段都是 unchanged / keep_db。
      </p>
    );
  }
  return (
    <div className="space-y-3 p-2">
      {interesting.map(({ field, entries }) => (
        <div key={field} className="rounded-md border border-border/60 bg-card p-2 text-xs">
          <div className="flex items-center justify-between mb-1">
            <span className="font-mono font-medium">{field}</span>
            <span className="text-muted-foreground">{entries.length} 行将写入</span>
          </div>
          <ul className="space-y-1 max-h-40 overflow-y-auto">
            {entries.map(({ row, f }) => (
              <li key={row.row_index} className="flex items-center gap-2">
                <span className="text-muted-foreground w-10 shrink-0 font-mono">
                  #{row.row_index}
                </span>
                <span className="font-mono w-32 truncate shrink-0">{row.product_code}</span>
                <ActionChip action={f.action} />
                <span className="font-mono text-[11px] text-muted-foreground">
                  {f.action === "change"
                    ? `${renderValue(f.db)} → ${renderValue(f.excel)}`
                    : `(空) → ${renderValue(f.excel)}`}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

// ─── Summary header ─────────────────────────────────────────

function SummaryBar({ data }: { data: UploadDiffArtifactPayload }) {
  const { summary } = data;
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs px-3 py-2 bg-zinc-50 dark:bg-zinc-900 border-b border-border/40">
      <span className="font-medium">batch #{data.batch_id}</span>
      <span className="text-muted-foreground">·</span>
      <span className="text-muted-foreground">{data.filename}</span>
      <span className="text-muted-foreground">·</span>
      <span>
        创建 <span className="font-mono">{summary.create}</span>
      </span>
      <span>
        更新 <span className="font-mono">{summary.update}</span>
      </span>
      <span>
        跳过 <span className="font-mono">{summary.skip}</span>
      </span>
      {summary.error > 0 && (
        <span className="text-rose-700">
          错 <span className="font-mono">{summary.error}</span>
        </span>
      )}
      {data.truncated && (
        <span className="text-amber-700 text-[10px]">(被截断)</span>
      )}
    </div>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap gap-2 px-3 py-1.5 text-[10px] text-muted-foreground border-b border-border/40">
      {Object.entries(ACTION_STYLE)
        .filter(([k]) => k !== "error" && k !== "immutable")
        .map(([k, s]) => (
          <span key={k} className="inline-flex items-center gap-1">
            <span className={`w-3 h-3 rounded-sm ${s.bg} flex items-center justify-center text-[8px] font-mono`}>
              {s.symbol}
            </span>
            <span>{k}</span>
          </span>
        ))}
    </div>
  );
}

// ─── Main component ─────────────────────────────────────────

export function UploadDiffViewer({ batchId, view }: Props) {
  const [data, setData] = useState<UploadDiffArtifactPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Agent's view config seeds the local mode, but the user can flip it.
  const [mode, setMode] = useState<ViewMode>(view?.mode || "table");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getUploadDiffArtifact(batchId)
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [batchId]);

  if (loading) {
    return (
      <div className="text-xs text-muted-foreground p-3 border rounded-md">
        加载 batch #{batchId} 的预览…
      </div>
    );
  }
  if (error) {
    return (
      <div className="text-xs text-destructive p-3 border border-destructive/30 rounded-md bg-destructive/5">
        加载失败: {error}
      </div>
    );
  }
  if (!data) return null;

  const allUpdate = data.update;
  const allCreate = data.create;
  const allSkip = data.skip;
  const allErr = data.error;
  // Apply filter only to update rows (other groups are sliced differently).
  const update = applyFilter(allUpdate, view?.filter);

  return (
    <div className="border rounded-md bg-card overflow-hidden">
      <SummaryBar data={data} />
      <Legend />

      <div className="flex items-center gap-1 px-3 py-1.5 text-[11px] border-b border-border/40 bg-background">
        <span className="text-muted-foreground mr-1">视图:</span>
        {(["table", "cards", "heatmap", "field_grouped"] as ViewMode[]).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`px-2 py-0.5 rounded ${
              mode === m
                ? "bg-zinc-900 text-zinc-100 dark:bg-zinc-100 dark:text-zinc-900"
                : "hover:bg-zinc-100 dark:hover:bg-zinc-800"
            }`}
          >
            {m}
          </button>
        ))}
        <span className="ml-auto text-muted-foreground">
          updates: {update.length}/{allUpdate.length} · creates: {allCreate.length} · skips: {allSkip.length} · errors: {allErr.length}
        </span>
      </div>

      <div className="max-h-[480px] overflow-auto">
        {mode === "table" && (
          <TableView rows={update} highlightRowIndex={view?.highlight?.row_index} />
        )}
        {mode === "cards" && (
          <CardsView rows={update} highlightRowIndex={view?.highlight?.row_index} />
        )}
        {mode === "heatmap" && <HeatmapView rows={update} />}
        {mode === "field_grouped" && <FieldGroupedView rows={update} />}
      </div>
    </div>
  );
}
