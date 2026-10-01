import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { WorkflowBatch, WorkflowRow } from "@/lib/product-upload-api";

interface Props {
  batch: WorkflowBatch;
  rows: WorkflowRow[];
  busy: boolean;
  onBack: () => void;
  onSave: () => void;
}
const display = (value: unknown) =>
  value === null || value === undefined || value === "" ? "—" : String(value);
export function DirectReviewStep({ batch, rows, busy, onBack, onSave }: Props) {
  const [page, setPage] = useState(0);
  const errors = rows.filter((row) => row.kind === "error");
  const canSave =
    batch.can_continue &&
    !batch.summary.error &&
    !batch.summary.create &&
    errors.length === 0 &&
    batch.summary.update > 0;
  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-base font-semibold">核对变更并保存</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          {batch.summary.update} 行变更 · {batch.summary.skip} 行无变化 ·{" "}
          {batch.summary.error} 行需修正。尚未写入产品数据。
        </p>
      </div>
      {errors.length > 0 && (
        <div
          role="alert"
          className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm"
        >
          <div className="font-medium">请返回编辑修正以下问题</div>
          <ul className="mt-2 max-h-52 space-y-2 overflow-auto">
            {errors.map((row) => (
              <li key={row.staging_id}>
                数据行 {row.source_row_number} · {row.identity.product_code}{" "}
                {row.identity.product_name}：
                {row.issues.map((issue) => issue.message).join("；") ||
                  row.reason ||
                  "检查未通过"}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>数据行 / 产品</TableHead>
              <TableHead>字段</TableHead>
              <TableHead>原值</TableHead>
              <TableHead>新值</TableHead>
              <TableHead>检查结果</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.slice(page * 20, page * 20 + 20).map((row) => {
              const fields = row.fields.filter((field) => field.changed);
              return (
                <TableRow key={row.staging_id}>
                  <TableCell className="max-w-72 whitespace-normal">
                    {row.source_row_number} · {row.identity.product_code || "—"}
                    <div className="mt-1">{row.identity.product_name}</div>
                  </TableCell>
                  <TableCell>
                    {fields.map((field) => (
                      <div key={field.key} className="min-h-7">
                        {field.label}
                      </div>
                    ))}
                  </TableCell>
                  <TableCell>
                    {fields.map((field) => (
                      <div key={field.key} className="min-h-7">
                        {display(field.before)}
                      </div>
                    ))}
                  </TableCell>
                  <TableCell className="bg-amber-50/70">
                    {fields.map((field) => (
                      <div key={field.key} className="min-h-7">
                        {display(field.after)}
                      </div>
                    ))}
                  </TableCell>
                  <TableCell className="max-w-72 whitespace-normal">
                    {row.kind === "error" ? (
                      <span className="text-destructive">
                        {row.issues.map((issue) => issue.message).join("；") ||
                          row.reason ||
                          "检查未通过"}
                      </span>
                    ) : row.kind === "skip" ? (
                      "无变化"
                    ) : (
                      "通过"
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
        <div className="flex items-center justify-end gap-3 border-t p-3 text-xs">
          <Button
            variant="outline"
            size="sm"
            disabled={page === 0}
            onClick={() => setPage(page - 1)}
          >
            上一页
          </Button>
          {page + 1} / {Math.max(1, Math.ceil(rows.length / 20))}
          <Button
            variant="outline"
            size="sm"
            disabled={(page + 1) * 20 >= rows.length}
            onClick={() => setPage(page + 1)}
          >
            下一页
          </Button>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        确认后仅保存本次变更，并保留处理记录；其他字段和区间不变。若期间重叠或数据已被他人更新，本批次不会写入。
      </p>
      <div className="flex justify-between border-t pt-4">
        <Button variant="outline" disabled={busy} onClick={onBack}>
          返回编辑
        </Button>
        <Button disabled={busy || !canSave} onClick={onSave}>
          {busy ? "正在保存…" : "确认保存"}
        </Button>
      </div>
    </section>
  );
}
