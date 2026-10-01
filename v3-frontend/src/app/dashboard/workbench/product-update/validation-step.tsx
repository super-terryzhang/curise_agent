"use client";

import { AlertCircle, CheckCircle2, FileWarning, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { issueSuggestion } from "@/lib/existing-product-update-workflow";
import type { WorkflowBatch, WorkflowRow } from "@/lib/product-upload-api";

interface ValidationStepProps {
  batch: WorkflowBatch | null;
  rows: WorkflowRow[];
  validating: boolean;
  error: string | null;
  onBack: () => void;
  onRetry: () => void;
  onContinue: () => void;
}

export function ValidationStep({ batch, rows, validating, error, onBack, onRetry, onContinue }: ValidationStepProps) {
  const rowIssues = rows.flatMap((row) => row.issues.map((issue) => ({ row, issue })));
  const headerIssues = batch?.header_diagnostics.blocking_issues ?? [];
  const passed = Boolean(batch?.can_continue) && rowIssues.length === 0 && headerIssues.length === 0 && !error;

  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-base font-semibold">程序检查</h2>
        <p className="mt-1 text-sm text-muted-foreground">系统只负责检查文件结构、数据格式、产品身份、价格区间和版本条件；此时不会写入产品数据。</p>
      </div>

      {validating && (
        <div className="flex min-h-44 flex-col items-center justify-center rounded-md border bg-muted/20 text-sm text-muted-foreground">
          <RotateCcw className="mb-3 size-6 animate-spin" />正在检查上传文件…
        </div>
      )}

      {!validating && passed && batch && (
        <div className="flex items-start gap-3 rounded-md border border-emerald-200 bg-emerald-50 px-4 py-4 text-emerald-900">
          <CheckCircle2 className="mt-0.5 size-5 shrink-0" />
          <div>
            <div className="font-semibold">程序检查通过</div>
            <div className="mt-1 text-sm">{batch.summary.update} 行更新，{batch.summary.skip} 行无变化。请进入下一步核对实际变更。</div>
          </div>
        </div>
      )}

      {!validating && (error || headerIssues.length > 0 || rowIssues.length > 0) && (
        <>
          <div className="flex items-start gap-3 rounded-md border border-destructive/30 bg-destructive/5 px-4 py-4 text-destructive">
            <FileWarning className="mt-0.5 size-5 shrink-0" />
            <div>
              <div className="font-semibold">文件未通过检查</div>
              <div className="mt-1 text-sm">请按下方原因修改 Excel，然后返回上一步重新上传。系统尚未写入任何产品数据。</div>
            </div>
          </div>

          {error && <div className="rounded-md border px-4 py-3 text-sm text-destructive">{error}</div>}

          {(headerIssues.length > 0 || rowIssues.length > 0) && (
            <div className="overflow-hidden rounded-md border">
              <div className="grid grid-cols-[120px_minmax(160px,1fr)_minmax(240px,2fr)_minmax(180px,1fr)] gap-3 border-b bg-muted/40 px-4 py-2 text-xs font-semibold">
                <span>位置</span><span>产品</span><span>问题原因</span><span>修改建议</span>
              </div>
              {headerIssues.map((issue, index) => (
                <div key={`${issue.code}-${index}`} className="grid grid-cols-[120px_minmax(160px,1fr)_minmax(240px,2fr)_minmax(180px,1fr)] gap-3 border-b px-4 py-3 text-sm last:border-b-0">
                  <span>表头</span><span>—</span><span>{issue.message}</span><span>按问题说明恢复标准表头</span>
                </div>
              ))}
              {rowIssues.map(({ row, issue }) => (
                <div key={`${row.staging_id}-${issue.code}-${issue.field ?? "row"}`} className="grid grid-cols-[120px_minmax(160px,1fr)_minmax(240px,2fr)_minmax(180px,1fr)] gap-3 border-b px-4 py-3 text-sm last:border-b-0">
                  <span>Excel 第 {row.source_row_number} 行</span>
                  <span><span className="block font-mono text-xs">{row.identity.product_code || "—"}</span><span className="block text-xs text-muted-foreground">{row.identity.product_name || "—"}</span></span>
                  <span className="text-destructive">{issue.message}</span>
                  <span>{issueSuggestion(issue)}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      <div className="flex justify-between border-t pt-4">
        <Button variant="outline" disabled={validating} onClick={onBack}>{passed ? "上一步" : "返回修改并重新上传"}</Button>
        {passed ? (
          <Button onClick={onContinue}>核对变更</Button>
        ) : (
          <Button variant="outline" disabled={validating || !batch} onClick={onRetry}>重新检查</Button>
        )}
      </div>
    </section>
  );
}
