"use client";

import { useCallback, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  Download,
  Loader2,
  RefreshCw,
  UploadCloud,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import {
  cancelProductBatch,
  commitProductBatch,
  getProductBatchRows,
  productUploadTemplateUrl,
  uploadProductWorkbook,
  validateProductBatch,
  type CommitResult,
  type WorkflowBatch,
  type WorkflowRow,
  type WorkflowRowsPage,
} from "@/lib/product-upload-api";
import { cn } from "@/lib/utils";
import { UploadHistory } from "./UploadHistory";

const STEPS = ["上传文件", "程序检查", "核对变更", "确认提交"];

function valueText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value).replace("T00:00:00", "");
}

function StepBar({ current }: { current: number }) {
  return (
    <div className="grid grid-cols-4 border-b bg-muted/20 px-5 py-4">
      {STEPS.map((label, index) => {
        const number = index + 1;
        const done = number < current;
        const active = number === current;
        return (
          <div key={label} className="flex items-center">
            <div
              className={cn(
                "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
                done && "border-foreground bg-foreground text-background",
                active && "border-primary bg-primary text-primary-foreground",
                !done && !active && "bg-background text-muted-foreground",
              )}
            >
              {done ? <Check className="h-3.5 w-3.5" /> : number}
            </div>
            <span className={cn("ml-2 text-xs", active ? "font-semibold" : "text-muted-foreground")}>{label}</span>
            {number < 4 && <div className="mx-3 h-px flex-1 bg-border" />}
          </div>
        );
      })}
    </div>
  );
}

function Summary({ batch }: { batch: WorkflowBatch }) {
  const stats = [
    ["总行数", batch.summary.total],
    ["新增", batch.summary.create],
    ["更新", batch.summary.update],
    ["无变化", batch.summary.skip],
    ["错误", batch.summary.error],
  ];
  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border bg-border sm:grid-cols-5">
      {stats.map(([label, value]) => (
        <div key={label} className="bg-background px-4 py-3">
          <div className="text-[11px] text-muted-foreground">{label}</div>
          <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
        </div>
      ))}
    </div>
  );
}

function Pagination({ page, onPage }: { page: WorkflowRowsPage; onPage: (value: number) => void }) {
  if (page.total_pages <= 1) return null;
  return (
    <div className="flex items-center justify-end gap-2 pt-3 text-xs text-muted-foreground">
      <span>{page.page} / {page.total_pages} 页</span>
      <Button variant="outline" size="sm" disabled={page.page <= 1} onClick={() => onPage(page.page - 1)}>上一页</Button>
      <Button variant="outline" size="sm" disabled={page.page >= page.total_pages} onClick={() => onPage(page.page + 1)}>下一页</Button>
    </div>
  );
}

export default function ProductUploadPage() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [step, setStep] = useState(1);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [batch, setBatch] = useState<WorkflowBatch | null>(null);
  const [rows, setRows] = useState<WorkflowRowsPage | null>(null);
  const [result, setResult] = useState<CommitResult | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);

  const loadRows = useCallback(async (target: WorkflowBatch, view: "issues" | "changes", page = 1) => {
    const next = await getProductBatchRows(target.id, { view, page, pageSize: 25, changedOnly: true });
    setRows(next);
  }, []);

  const handleFile = useCallback(async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      toast.error("产品数据上传只接受 .xlsx 文件");
      return;
    }
    setBusy(true);
    setRequestError(null);
    setResult(null);
    try {
      const uploaded = await uploadProductWorkbook(file);
      setBatch(uploaded);
      setStep(2);
      const checked = await validateProductBatch(uploaded.id);
      setBatch(checked);
      await loadRows(checked, "issues");
    } catch (error) {
      const message = error instanceof Error ? error.message : "文件处理失败";
      setRequestError(message);
      toast.error(message);
    } finally {
      setBusy(false);
    }
  }, [loadRows]);

  const reset = useCallback(async () => {
    if (batch?.status === "resolved") {
      await cancelProductBatch(batch.id).catch(() => undefined);
    }
    setStep(1);
    setBatch(null);
    setRows(null);
    setResult(null);
    setRequestError(null);
    if (inputRef.current) inputRef.current.value = "";
  }, [batch]);

  const goToReview = useCallback(async () => {
    if (!batch?.can_continue) return;
    setBusy(true);
    try {
      await loadRows(batch, "changes");
      setStep(3);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "读取变更失败");
    } finally {
      setBusy(false);
    }
  }, [batch, loadRows]);

  const submit = useCallback(async () => {
    if (!batch) return;
    setBusy(true);
    setRequestError(null);
    try {
      const committed = await commitProductBatch(batch.id);
      setResult(committed);
      setBatch({ ...batch, status: "completed", can_continue: false });
    } catch (error) {
      const message = error instanceof Error ? error.message : "提交失败";
      setRequestError(message);
      toast.error(message);
    } finally {
      setBusy(false);
    }
  }, [batch]);

  const changePage = async (page: number) => {
    if (!batch) return;
    setBusy(true);
    try {
      await loadRows(batch, step === 2 ? "issues" : "changes", page);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto bg-muted/20">
      <div className="mx-auto max-w-6xl px-6 py-6">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <button className="mb-2 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground" onClick={() => router.push("/dashboard/workbench")}>
              <ArrowLeft className="h-3.5 w-3.5" /> 返回工作台
            </button>
            <h1 className="text-lg font-semibold">产品数据上传</h1>
            <p className="mt-1 text-xs text-muted-foreground">上传、检查、核对、提交；程序检查不通过时不会写入产品数据。</p>
          </div>
          {batch && <Button variant="outline" size="sm" onClick={reset} disabled={busy}><RefreshCw className="mr-1.5 h-3.5 w-3.5" />重新上传</Button>}
        </div>

        <Card className="gap-0 overflow-hidden rounded-md py-0 shadow-sm">
          <StepBar current={step} />
          <CardContent className="p-6">
            {busy && <Progress value={step === 1 ? 35 : step === 2 ? 70 : 90} className="mb-5 h-1" />}

            {step === 1 && (
              <div>
                <div
                  className={cn("flex min-h-72 cursor-pointer flex-col items-center justify-center rounded-md border border-dashed bg-background p-8 text-center transition-colors", dragging && "border-primary bg-primary/5")}
                  onClick={() => inputRef.current?.click()}
                  onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={(event) => { event.preventDefault(); setDragging(false); const file = event.dataTransfer.files[0]; if (file) void handleFile(file); }}
                >
                  <input ref={inputRef} type="file" accept=".xlsx" className="hidden" onChange={(event) => { const file = event.target.files?.[0]; if (file) void handleFile(file); }} />
                  {busy ? <Loader2 className="h-9 w-9 animate-spin text-muted-foreground" /> : <UploadCloud className="h-9 w-9 text-muted-foreground" />}
                  <div className="mt-4 text-sm font-medium">拖放 Excel 到这里，或点击选择文件</div>
                  <div className="mt-2 text-xs text-muted-foreground">仅支持 .xlsx；系统会保存原件并检查文件大小与内容。</div>
                  <Button className="mt-5" disabled={busy}>选择 Excel</Button>
                </div>
                <div className="mt-4 flex items-center justify-between rounded-md border bg-muted/20 px-4 py-3 text-xs">
                  <span className="text-muted-foreground">第一次使用建议先填写标准模板。</span>
                  <a href={productUploadTemplateUrl()} className="flex items-center gap-1.5 font-medium text-primary hover:underline"><Download className="h-3.5 w-3.5" />下载模板</a>
                </div>
              </div>
            )}

            {step === 2 && batch && (
              <div className="space-y-5">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h2 className="text-base font-semibold">程序检查结果</h2>
                    <p className="mt-1 text-xs text-muted-foreground">{batch.filename} · {batch.sheet_name || "首个工作表"} · 表头第 {batch.header_row_number || 1} 行</p>
                  </div>
                  <Badge variant={batch.can_continue ? "outline" : "destructive"}>{batch.can_continue ? "检查通过" : "需要修改文件"}</Badge>
                </div>
                <Summary batch={batch} />
                {batch.header_diagnostics.blocking_issues.length > 0 && (
                  <section>
                    <h3 className="mb-2 text-xs font-semibold">表头问题</h3>
                    <div className="divide-y rounded-md border bg-background">
                      {batch.header_diagnostics.blocking_issues.map((issue, index) => (
                        <div key={`${issue.code}-${index}`} className="flex gap-3 px-4 py-3 text-sm"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" /><div><div>{issue.message}</div><div className="mt-1 text-xs text-muted-foreground">请修改 Excel 表头后重新上传。</div></div></div>
                      ))}
                    </div>
                  </section>
                )}
                <section>
                  <h3 className="mb-2 text-xs font-semibold">数据行问题</h3>
                  {rows?.items.length ? (
                    <div className="overflow-hidden rounded-md border bg-background">
                      <div className="grid grid-cols-[90px_1fr_2fr] border-b bg-muted/40 px-4 py-2 text-[11px] font-medium text-muted-foreground"><span>Excel 行</span><span>产品</span><span>原因</span></div>
                      {rows.items.map((row) => <div key={row.staging_id} className="grid grid-cols-[90px_1fr_2fr] gap-3 border-b px-4 py-3 text-sm last:border-0"><span className="font-mono">{row.source_row_number}</span><span>{row.identity.product_name || "—"}</span><div className="space-y-1 text-destructive">{row.issues.map((issue, index) => <div key={index}>{issue.message}</div>)}</div></div>)}
                    </div>
                  ) : <div className="flex items-center gap-2 rounded-md border bg-emerald-50/60 px-4 py-4 text-sm text-emerald-800"><CheckCircle2 className="h-4 w-4" />全部数据行检查通过</div>}
                  {rows && <Pagination page={rows} onPage={(value) => void changePage(value)} />}
                </section>
                <div className="flex justify-between border-t pt-4"><Button variant="outline" onClick={reset}>返回并重新上传</Button><Button disabled={!batch.can_continue || busy} onClick={() => void goToReview()}>核对变更 <ArrowRight className="ml-1.5 h-4 w-4" /></Button></div>
              </div>
            )}

            {step === 3 && batch && (
              <div className="space-y-5">
                <div><h2 className="text-base font-semibold">核对变更</h2><p className="mt-1 text-xs text-muted-foreground">左侧是当前数据，右侧是 Excel 将写入的数据；有变化的字段已高亮。</p></div>
                <Summary batch={batch} />
                <div className="space-y-3">
                  {rows?.items.map((row) => <ChangeRow key={row.staging_id} row={row} />)}
                  {!rows?.items.length && <div className="rounded-md border bg-background p-6 text-center text-sm text-muted-foreground">没有需要写入的变更</div>}
                </div>
                {rows && <Pagination page={rows} onPage={(value) => void changePage(value)} />}
                <div className="flex justify-between border-t pt-4"><Button variant="outline" onClick={() => setStep(2)}>返回检查结果</Button><Button onClick={() => setStep(4)} disabled={!rows}>进入确认 <ArrowRight className="ml-1.5 h-4 w-4" /></Button></div>
              </div>
            )}

            {step === 4 && batch && (
              <div className="mx-auto max-w-3xl space-y-5">
                {result ? (
                  <div className="py-8 text-center"><CheckCircle2 className="mx-auto h-11 w-11 text-emerald-600" /><h2 className="mt-4 text-lg font-semibold">产品数据已提交</h2><p className="mt-2 text-sm text-muted-foreground">新增 {result.created} 条，更新 {result.updated} 条，无变化 {result.skipped} 条，失败 {result.errors} 条。</p><div className="mt-6 flex justify-center gap-2"><Button variant="outline" onClick={() => router.push("/dashboard/data")}>查看产品数据</Button><Button onClick={reset}>上传另一个文件</Button></div></div>
                ) : (
                  <><div><h2 className="text-base font-semibold">确认提交</h2><p className="mt-1 text-xs text-muted-foreground">这是唯一一次提交确认。点击后才会写入产品数据库，并记录变更日志。</p></div><Summary batch={batch} /><div className="rounded-md border bg-amber-50/70 px-4 py-3 text-sm text-amber-900">将新增 {batch.summary.create} 条、更新 {batch.summary.update} 条；{batch.summary.skip} 条无变化数据不会重复写入。</div>{requestError && <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{requestError}</div>}<div className="flex justify-between border-t pt-4"><Button variant="outline" onClick={() => setStep(3)} disabled={busy}>返回核对</Button><Button onClick={() => void submit()} disabled={busy}>{busy && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}确认并提交</Button></div></>
                )}
              </div>
            )}

            {requestError && step < 4 && <div className="mt-4 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{requestError}</div>}
          </CardContent>
        </Card>
        {step === 1 && <UploadHistory />}
      </div>
    </div>
  );
}

function ChangeRow({ row }: { row: WorkflowRow }) {
  const displayFieldValue = (value: unknown, currency: string | null) => {
    const text = valueText(value);
    return currency && text !== "—" ? `${currency} ${text}` : text;
  };
  return (
    <Card className="gap-0 rounded-md py-0 shadow-none">
      <CardHeader className="flex-row items-center justify-between border-b bg-muted/25 px-4 py-3">
        <CardTitle className="text-sm"><span className="mr-2 font-mono text-xs text-muted-foreground">第 {row.source_row_number} 行</span>{row.identity.product_name || "未命名产品"}</CardTitle>
        <Badge variant={row.kind === "create" ? "default" : "outline"}>{row.kind === "create" ? "新增" : "更新"}</Badge>
      </CardHeader>
      <CardContent className="p-0">
        <div className="grid grid-cols-[150px_1fr_1fr] border-b bg-muted/20 px-4 py-2 text-[11px] font-medium text-muted-foreground"><span>字段</span><span>当前数据</span><span>Excel 数据</span></div>
        {row.fields.map((field) => <div key={field.key} className={cn("grid grid-cols-[150px_1fr_1fr] border-b px-4 py-2.5 text-sm last:border-0", field.changed && "bg-amber-50/60")}><span className="text-muted-foreground">{field.label}</span><span>{displayFieldValue(field.before, field.currency)}</span><span className={cn(field.changed && "font-medium text-foreground")}>{displayFieldValue(field.after, field.currency)}</span></div>)}
      </CardContent>
    </Card>
  );
}
