"use client";

import { AlertTriangle, Check, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { flattenChangedFields, operationSummary } from "@/lib/existing-product-update-workflow";
import type { WorkflowBatch, WorkflowRow } from "@/lib/product-upload-api";

interface ReviewStepProps {
  batch: WorkflowBatch;
  rows: WorkflowRow[];
  committing: boolean;
  error: string | null;
  confirmOpen: boolean;
  onConfirmOpenChange: (open: boolean) => void;
  onBack: () => void;
  onCommit: () => void;
}

function displayValue(value: unknown, currency: string | null): string {
  if (value === null || value === undefined || value === "") return "—";
  const text = typeof value === "boolean" ? (value ? "有效" : "无效") : String(value);
  return currency ? `${text} ${currency}` : text;
}

export function ReviewStep({ batch, rows, committing, error, confirmOpen, onConfirmOpenChange, onBack, onCommit }: ReviewStepProps) {
  const changes = flattenChangedFields(rows);
  const summary = operationSummary(rows);
  const containsCreate = rows.some((item) => item.kind === "create");
  const canCommit = batch.can_continue && !containsCreate && changes.length > 0 && !committing;

  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-base font-semibold">核对变更并提交</h2>
        <p className="mt-1 text-sm text-muted-foreground">左侧是数据库当前值，右侧是文件中的新值。只有点击提交后才会写入。</p>
      </div>

      <div className="grid gap-2 sm:grid-cols-3">
        {Object.entries(summary).map(([operation, count]) => (
          <div key={operation} className="rounded-md border bg-muted/20 px-3 py-2 text-sm"><span className="text-muted-foreground">{operation}</span><strong className="float-right">{count}</strong></div>
        ))}
      </div>

      {containsCreate && (
        <div className="flex items-start gap-3 rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <span>文件包含新增产品，本流程只允许更新已有产品。请移除新增行，或改用“产品数据上传”。</span>
        </div>
      )}
      {error && <div className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">{error}</div>}

      <div className="overflow-hidden rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Excel 行</TableHead>
              <TableHead>产品</TableHead>
              <TableHead>操作</TableHead>
              <TableHead>字段</TableHead>
              <TableHead className="bg-muted/30">变更前</TableHead>
              <TableHead className="bg-amber-50">变更后</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {changes.map((change) => (
              <TableRow key={`${change.stagingId}-${change.field}-${change.operation}`}>
                <TableCell>{change.sourceRowNumber}</TableCell>
                <TableCell><span className="block font-mono text-xs">{change.productCode || "—"}</span><span className="block max-w-[220px] whitespace-normal text-xs text-muted-foreground">{change.productName || "—"}</span></TableCell>
                <TableCell>{change.operation}</TableCell>
                <TableCell>{change.field}</TableCell>
                <TableCell className="bg-muted/20">{displayValue(change.before, change.currency)}</TableCell>
                <TableCell className="bg-amber-50/70 font-medium">{displayValue(change.after, change.currency)}</TableCell>
              </TableRow>
            ))}
            {changes.length === 0 && <TableRow><TableCell colSpan={6} className="h-24 text-center text-muted-foreground">没有可提交的变更</TableCell></TableRow>}
          </TableBody>
        </Table>
      </div>

      <div className="flex items-start gap-2 rounded-md border bg-muted/20 px-3 py-2 text-sm text-muted-foreground">
        <ShieldCheck className="mt-0.5 size-4 shrink-0" />提交时会再次检查产品版本和价格区间；如果数据已经被其他人修改，本次提交会停止并显示冲突原因。
      </div>

      <div className="flex justify-between border-t pt-4">
        <Button variant="outline" disabled={committing} onClick={onBack}>返回检查结果</Button>
        <Button disabled={!canCommit} onClick={() => onConfirmOpenChange(true)}>{committing ? "正在提交…" : "提交更新"}</Button>
      </div>

      {confirmOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="alertdialog" aria-modal="true" aria-labelledby="existing-product-update-confirm-title">
          <div className="w-full max-w-md rounded-lg border bg-background p-6 shadow-xl">
            <div className="flex size-10 items-center justify-center rounded-full bg-amber-100 text-amber-800"><AlertTriangle className="size-5" /></div>
            <h3 id="existing-product-update-confirm-title" className="mt-4 text-base font-semibold">确认提交更新</h3>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">即将把 {changes.length} 项字段变更写入现有产品。提交时会再次检查产品版本和价格区间，冲突数据不会被覆盖。</p>
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="outline" disabled={committing} onClick={() => onConfirmOpenChange(false)}>取消</Button>
              <Button disabled={!canCommit} onClick={onCommit}><Check />{committing ? "正在提交…" : "确认提交更新"}</Button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
