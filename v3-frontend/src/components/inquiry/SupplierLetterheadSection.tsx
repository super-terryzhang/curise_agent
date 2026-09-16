"use client";

/**
 * SupplierLetterheadSection — per-supplier "9 fields that will be printed
 * on the inquiry Excel" panel, shown on the inquiry tab's detail pane.
 *
 * Semantics that distinguish this from the existing template-field
 * panel right below it:
 *
 *   - Template-field panel writes order-scoped overrides
 *     (`order_metadata.supplier_overrides[supplier_id]`). The override
 *     lasts for this order only.
 *
 *   - This section writes the supplier MASTER row directly via
 *     PATCH /api/data/suppliers/{id}. Edits are permanent and benefit
 *     all future inquiries to the same supplier.
 *
 * The two are visually distinct (different background tint, "永久" tag)
 * so the user doesn't conflate "fix once" with "fix for this order".
 *
 * Inline edits are debounced 800ms — matches the existing FieldRow
 * pattern in SupplierInquiryCard so the UX feels consistent. Errors
 * surface via sonner toast; the section keeps the latest typed value
 * so a transient network blip doesn't lose work.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Loader2,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { updateSupplier } from "@/lib/data-api";
import type { SupplierLetterheadField } from "@/lib/orders-api";

export interface SupplierLetterheadSectionProps {
  supplierId: number;
  /** The 9 letterhead entries straight off `SupplierReadiness.supplier_letterhead`.
   * Optional — older backends may omit this; the section degrades gracefully. */
  letterhead?: SupplierLetterheadField[];
  /** True if the current user can write supplier master data
   * (superadmin / admin). Non-writers see read-only rows. */
  canEdit: boolean;
  /** Called after a successful PATCH so the parent page can refetch
   * readiness and re-render with the new values. */
  onSaved: () => void;
}

type SaveState = "idle" | "saving" | "saved" | "error";

export default function SupplierLetterheadSection({
  supplierId,
  letterhead,
  canEdit,
  onSaved,
}: SupplierLetterheadSectionProps) {
  // localEdits keys are letterhead `column` names (matching the suppliers
  // DB column + the PATCH payload key). Values are the current input
  // text, even before save fires.
  const [localEdits, setLocalEdits] = useState<Record<string, string>>({});
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveMessage, setSaveMessage] = useState("");

  // Reset local edits whenever the selected supplier or its letterhead
  // changes — otherwise switching between suppliers in the sidebar
  // carries stale typed-but-unsaved text across.
  useEffect(() => {
    setLocalEdits({});
    setSaveState("idle");
    setSaveMessage("");
  }, [supplierId, letterhead]);

  // Ref carries the latest debounced payload so the timer's closure
  // doesn't fire with stale state.
  const pendingRef = useRef<Record<string, string>>({});
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flushSave = useCallback(async () => {
    const payload = pendingRef.current;
    if (Object.keys(payload).length === 0) return;
    // `updateSupplier` accepts string | null per Partial<SupplierWritable>.
    // An empty trimmed input becomes null so users can wipe a wrong value.
    const cleaned: Record<string, string | null> = {};
    for (const [k, v] of Object.entries(payload)) {
      const t = v.trim();
      cleaned[k] = t === "" ? null : t;
    }
    setSaveState("saving");
    setSaveMessage("保存中");
    try {
      await updateSupplier(
        supplierId,
        cleaned as Parameters<typeof updateSupplier>[1],
      );
      pendingRef.current = {};
      setSaveState("saved");
      setSaveMessage(
        `已保存 ${new Date().toLocaleTimeString("zh-CN", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        })}`,
      );
      onSaved();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "保存失败";
      setSaveState("error");
      setSaveMessage(msg);
      toast.error(`供应商信头保存失败：${msg}`);
    }
  }, [supplierId, onSaved]);

  const handleChange = useCallback(
    (column: string, value: string) => {
      setLocalEdits((prev) => ({ ...prev, [column]: value }));
      pendingRef.current = { ...pendingRef.current, [column]: value };
      if (timerRef.current) clearTimeout(timerRef.current);
      // 800ms debounce — matches FieldRow's existing rhythm in
      // SupplierInquiryCard so the two sections feel uniform.
      timerRef.current = setTimeout(flushSave, 800);
    },
    [flushSave],
  );

  // Clean up the debounce timer on unmount so a save isn't fired against
  // a stale supplier_id after the user navigates away.
  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  if (!letterhead || letterhead.length === 0) {
    // Older backends may not ship this — show nothing rather than a
    // confused empty section.
    return null;
  }

  const filledCount = letterhead.filter((f) => f.filled).length;
  const missingCount = letterhead.length - filledCount;

  return (
    <div className="px-5 py-4 border-b shrink-0 bg-slate-50/60 dark:bg-slate-950/30">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium">供应商主数据</span>
          <span
            className="text-[10px] text-muted-foreground"
            title="改这里的值会永久写入供应商主数据（影响所有未来的询价单）。
本订单临时改某个字段请用下方的「表头字段」。"
          >
            永久 · 印在询价 Excel 信头
          </span>
          {missingCount > 0 ? (
            <span className="text-[10px] text-amber-700 dark:text-amber-400 flex items-center gap-0.5">
              <AlertTriangle className="h-3 w-3" />
              缺 {missingCount}
            </span>
          ) : (
            <span className="text-[10px] text-emerald-700 dark:text-emerald-400 flex items-center gap-0.5">
              <CheckCircle2 className="h-3 w-3" />
              完整
            </span>
          )}
          {saveMessage && (
            <span
              className={`text-[10px] flex items-center gap-0.5 ${
                saveState === "error"
                  ? "text-destructive"
                  : saveState === "saved"
                  ? "text-emerald-600 dark:text-emerald-400"
                  : "text-muted-foreground/60"
              }`}
            >
              {saveState === "saving" ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : saveState === "saved" ? (
                <CheckCircle2 className="h-3 w-3" />
              ) : saveState === "error" ? (
                <XCircle className="h-3 w-3" />
              ) : null}
              {saveMessage}
            </span>
          )}
        </div>
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs gap-1"
          asChild
          title="跳转到数据管理打开完整编辑表单"
        >
          <a
            href={`/dashboard/data?tab=suppliers&edit=${supplierId}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            <ExternalLink className="h-3 w-3" />
            完整编辑
          </a>
        </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
        {letterhead.map((f) => {
          // Prefer the locally-typed value (debounced save not yet
          // flushed) over the server-known value so the user always sees
          // their own input even mid-save.
          const liveValue =
            localEdits[f.column] !== undefined
              ? localEdits[f.column]
              : f.value ?? "";
          const filledNow =
            localEdits[f.column] !== undefined
              ? localEdits[f.column].trim() !== ""
              : f.filled;

          if (!canEdit) {
            // Read-only display for non-Admin viewers.
            return (
              <div
                key={f.column}
                className={`flex items-center gap-3 px-3 py-1.5 rounded-md text-xs ${
                  filledNow
                    ? "bg-emerald-50 dark:bg-emerald-950/30 text-emerald-700 dark:text-emerald-400"
                    : "bg-amber-50 dark:bg-amber-950/30 text-amber-700 dark:text-amber-400"
                }`}
              >
                {filledNow ? (
                  <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                ) : (
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                )}
                <span className="w-24 shrink-0">{f.label}</span>
                <span className="flex-1 truncate">
                  {filledNow ? liveValue : "（未填写，请联系管理员补充）"}
                </span>
              </div>
            );
          }

          return (
            <div
              key={f.column}
              className={`flex items-center gap-3 px-3 py-1.5 rounded-md text-xs ${
                filledNow
                  ? "bg-emerald-50 dark:bg-emerald-950/30"
                  : "bg-amber-50 dark:bg-amber-950/40"
              }`}
            >
              {filledNow ? (
                <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
              ) : (
                <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-700 dark:text-amber-400" />
              )}
              <span
                className={`w-24 shrink-0 ${
                  filledNow
                    ? "text-emerald-700 dark:text-emerald-400"
                    : "text-amber-700 dark:text-amber-400"
                }`}
              >
                {f.label}
              </span>
              <input
                className="flex-1 min-w-0 bg-white dark:bg-background border border-border rounded px-2 py-0.5 text-xs text-foreground outline-none focus:border-primary focus:ring-1 focus:ring-primary/20 transition-colors"
                placeholder={`输入${f.label}...`}
                value={liveValue}
                onChange={(e) => handleChange(f.column, e.target.value)}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}
