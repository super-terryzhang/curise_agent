"use client";

/**
 * ApprovalCard — renders a Tier-2 action that the agent has proposed but
 * not yet executed. Triggered by the v3 backend's `approval_request` SSE
 * event. The user must explicitly approve or reject before the
 * destructive operation runs.
 *
 * The card is intentionally explicit about WHAT will happen — title +
 * summary + the raw payload. We don't try to prettify the payload by
 * action type because new actions can be added server-side without a
 * frontend release; better to show the truth than a stale-pretty view.
 */

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ShieldCheck, X, Loader2, AlertTriangle } from "lucide-react";

export interface PendingApproval {
  action_id: number;
  action: string;
  target_kind: string;
  target_id: number | null;
  summary: string;
  payload: Record<string, unknown>;
  // Filled in once decided
  status?: "pending" | "approved" | "rejected" | "failed";
  result?: Record<string, unknown> | null;
  error?: string;
}

interface ApprovalCardProps {
  approval: PendingApproval;
  onDecide: (
    actionId: number,
    decision: "approve" | "reject",
    notes?: string
  ) => Promise<void>;
}

const ACTION_DISPLAY_NAMES: Record<string, string> = {
  delete_order: "删除订单",
  delete_masterdata: "删除主数据",
  create_product: "创建产品",
  create_supplier: "创建供应商",
  update_product_financial: "更新产品（财务字段）",
  update_supplier_categories: "更新供应商品类",
  commit_upload_batch: "提交批量上传",
  rollback_upload_batch: "回滚批量上传",
};

const DESTRUCTIVE_ACTIONS = new Set([
  "delete_order",
  "delete_masterdata",
  "rollback_upload_batch",
]);

export function ApprovalCard({ approval, onDecide }: ApprovalCardProps) {
  const [busy, setBusy] = useState(false);
  const [localStatus, setLocalStatus] = useState(approval.status || "pending");

  const isDestructive = DESTRUCTIVE_ACTIONS.has(approval.action);
  const displayName = ACTION_DISPLAY_NAMES[approval.action] || approval.action;

  async function handle(decision: "approve" | "reject") {
    setBusy(true);
    try {
      await onDecide(approval.action_id, decision);
      setLocalStatus(decision === "approve" ? "approved" : "rejected");
    } catch {
      // onDecide is expected to surface errors via toast; we just reset busy
    } finally {
      setBusy(false);
    }
  }

  // Decided state — render as a compact "history" line
  if (localStatus !== "pending") {
    const tone =
      localStatus === "approved"
        ? "border-emerald-200 bg-emerald-50/50 text-emerald-800"
        : localStatus === "rejected"
        ? "border-zinc-200 bg-zinc-50 text-zinc-600"
        : "border-rose-200 bg-rose-50 text-rose-700";
    const label =
      localStatus === "approved"
        ? "已确认"
        : localStatus === "rejected"
        ? "已取消"
        : "执行失败";
    return (
      <div className={`rounded-lg border ${tone} px-3 py-2 text-xs`}>
        <div className="flex items-center gap-2">
          <span className="font-medium">{label}</span>
          <span className="opacity-70">·</span>
          <span className="truncate">{displayName}: {approval.summary}</span>
        </div>
      </div>
    );
  }

  return (
    <div
      className={`rounded-xl border-2 ${
        isDestructive
          ? "border-rose-300 bg-rose-50/60"
          : "border-amber-300 bg-amber-50/60"
      } p-4 shadow-sm space-y-3`}
    >
      <div className="flex items-start gap-2.5">
        <div
          className={`shrink-0 mt-0.5 rounded-full p-1.5 ${
            isDestructive ? "bg-rose-100 text-rose-600" : "bg-amber-100 text-amber-700"
          }`}
        >
          {isDestructive ? (
            <AlertTriangle className="h-4 w-4" />
          ) : (
            <ShieldCheck className="h-4 w-4" />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h4 className="font-semibold text-sm">需要确认：{displayName}</h4>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-background/60 border border-border/40 font-mono">
              #{approval.action_id}
            </span>
          </div>
          <p className="text-sm text-foreground/80 mt-1">{approval.summary}</p>

          {Object.keys(approval.payload || {}).length > 0 && (
            <details className="mt-2 group">
              <summary className="text-xs text-muted-foreground cursor-pointer hover:text-foreground select-none">
                查看详细参数 ▾
              </summary>
              <pre className="mt-1.5 text-[11px] bg-background/60 border border-border/40 rounded px-2 py-1.5 overflow-x-auto font-mono">
                {JSON.stringify(approval.payload, null, 2)}
              </pre>
            </details>
          )}
        </div>
      </div>

      <div className="flex items-center justify-end gap-2">
        <Button
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={() => handle("reject")}
          className="h-7 px-3 text-xs"
        >
          <X className="h-3.5 w-3.5 mr-1" />
          取消
        </Button>
        <Button
          variant={isDestructive ? "destructive" : "default"}
          size="sm"
          disabled={busy}
          onClick={() => handle("approve")}
          className="h-7 px-3 text-xs"
        >
          {busy ? (
            <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
          ) : (
            <ShieldCheck className="h-3.5 w-3.5 mr-1" />
          )}
          {isDestructive ? "确认删除" : "确认执行"}
        </Button>
      </div>
    </div>
  );
}
