"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Download, FileSpreadsheet, Loader2, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { WorkflowStepBar } from "@/components/workbench/workflow-step-bar";
import {
  commitImportWithRecovery,
  downloadProductTemplate,
  getDatabaseImportRows,
  getDatabaseSetupStatus,
  listSetupProducts,
  rollbackDatabaseImport,
  uploadDatabaseWorkbook,
  validateDatabaseImport,
  type CommitResult,
  type ImportBatch,
  type ImportRowsPage,
  type ProductPage,
  type SetupStatus,
} from "@/lib/database-setup-api";
import { ProductTable } from "./product-table";

const STEPS = ["准备", "下载模板", "上传检查", "核对变更", "确认结果"] as const;

function text(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function Summary({ batch }: { batch: ImportBatch }) {
  return (
    <div className="grid grid-cols-5 gap-px overflow-hidden rounded-md border bg-border">
      {[
        ["新增", batch.counts.create], ["更新", batch.counts.update],
        ["无变化", batch.counts.skip], ["警告", batch.counts.warning],
        ["阻止", batch.counts.block],
      ].map(([label, value]) => (
        <div className="bg-background px-4 py-3" key={label}>
          <div className="text-[11px] text-muted-foreground">{label}</div>
          <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
        </div>
      ))}
    </div>
  );
}

function IssueList({ rows }: { rows: ImportRowsPage }) {
  const issues = [
    ...rows.issues,
    ...rows.items.flatMap((row) => row.issues.map((issue) => ({ ...issue, sheet: issue.sheet || (row.sheet === "products" ? "产品资料" : "价格记录"), row: issue.row || row.row }))),
  ];
  if (!issues.length)
    return <div className="rounded-md border bg-emerald-50/60 p-4 text-sm text-emerald-800">全部检查通过，可以进入核对。</div>;
  return (
    <div className="overflow-hidden rounded-md border bg-background">
      <div className="grid grid-cols-[130px_70px_130px_1fr] border-b bg-muted/40 px-4 py-2 text-[11px] font-medium text-muted-foreground">
        <span>工作表</span><span>行</span><span>字段</span><span>原因</span>
      </div>
      {issues.map((issue, index) => (
        <div key={`${issue.code}-${index}`} className="grid grid-cols-[130px_70px_130px_1fr] border-b px-4 py-3 text-sm last:border-0">
          <span>{issue.sheet || "文件"}</span><span>{issue.row || "—"}</span>
          <span>{issue.field || "—"}</span>
          <span className={issue.severity === "block" ? "text-destructive" : "text-amber-700"}>{issue.message}</span>
        </div>
      ))}
    </div>
  );
}

function Review({ rows }: { rows: ImportRowsPage }) {
  return (
    <div className="space-y-3">
      {rows.items.filter((row) => row.action !== "skip").map((row) => (
        <div key={row.id} className="overflow-hidden rounded-md border bg-background">
          <div className="flex items-center justify-between border-b bg-muted/30 px-4 py-2 text-xs">
            <span>{row.sheet === "products" ? "产品资料" : "价格记录"} · 第 {row.row} 行 · {row.product_code || "—"}</span>
            <Badge variant="outline">{row.action === "create" ? "新增" : "更新"}</Badge>
          </div>
          <div className="grid grid-cols-2 divide-x">
            <div className="p-4"><div className="mb-2 text-[11px] font-medium text-muted-foreground">当前数据</div><pre className="whitespace-pre-wrap text-xs leading-5">{text(row.before_values)}</pre></div>
            <div className="bg-amber-50/40 p-4"><div className="mb-2 text-[11px] font-medium text-muted-foreground">导入后</div><pre className="whitespace-pre-wrap text-xs leading-5">{text(row.normalized_values)}</pre></div>
          </div>
        </div>
      ))}
    </div>
  );
}

export interface DatabaseSetupViewProps {
  step: number;
  status: SetupStatus;
  batch: ImportBatch | null;
  rows: ImportRowsPage | null;
  result: CommitResult | null;
  busy: boolean;
  error: string | null;
  selectedFile: File | null;
  onStep: (step: number) => void;
  onFile: (file: File) => void;
  onDownload: (includeExisting: boolean) => void;
  onRowsPage: (page: number) => void;
  onCommit: () => void;
  onRollback: () => void;
}

function Pagination({ page, pages, busy, onPage }: { page: number; pages: number; busy: boolean; onPage: (page: number) => void }) {
  if (pages <= 1) return null;
  return (
    <div className="flex items-center justify-end gap-2">
      <Button size="sm" variant="outline" disabled={busy || page <= 1} onClick={() => onPage(page - 1)}>上一页</Button>
      <span className="text-xs tabular-nums text-muted-foreground">{page} / {pages}</span>
      <Button size="sm" variant="outline" disabled={busy || page >= pages} onClick={() => onPage(page + 1)}>下一页</Button>
    </div>
  );
}

export function DatabaseSetupView(props: DatabaseSetupViewProps) {
  const { step, status, batch, rows, result, busy, error, selectedFile } = props;
  const safeTarget = status.enabled && status.database_name === "cruise_v3_clean";
  return (
    <Card className="gap-0 overflow-hidden rounded-md py-0 shadow-sm">
      <WorkflowStepBar labels={STEPS} current={step} />
      <CardContent className="space-y-5 p-6">
        {error && <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}</div>}
        {!safeTarget ? (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 p-5 text-sm text-destructive">临时环境未连接到指定的新数据库，导入操作已关闭。</div>
        ) : step === 1 ? (
          <>
            <div><h2 className="text-base font-semibold">新数据库准备状态</h2><p className="mt-1 text-xs text-muted-foreground">这里只整理新数据库，不影响当前生产系统。</p></div>
            <div className="grid gap-px overflow-hidden rounded-md border bg-border sm:grid-cols-5">
              {[["数据库", status.database_name], ["字段版本", status.schema_version ?? "—"], ["启用字段", status.field_count], ["产品", status.product_count], ["价格区间", status.price_period_count]].map(([label, value]) => <div key={label} className="bg-background px-4 py-3"><div className="text-[11px] text-muted-foreground">{label}</div><div className="mt-1 text-sm font-semibold">{value}</div></div>)}
            </div>
            <div className="flex items-center justify-between border-t pt-4">
              <Link className="text-sm font-medium text-primary hover:underline" href="/dashboard/settings/data-tables">配置产品字段</Link>
              <Button onClick={() => props.onStep(2)}>开始导入</Button>
            </div>
          </>
        ) : step === 2 ? (
          <>
            <div><h2 className="text-base font-semibold">下载当前结构的模板</h2><p className="mt-1 text-xs text-muted-foreground">一个 Excel 包含“产品资料”和“价格记录”两个工作表，请勿改名或移动表头。</p></div>
            <div className="grid gap-3 sm:grid-cols-2">
              <button className="rounded-md border bg-background p-5 text-left hover:border-primary/40" onClick={() => props.onDownload(false)}><Download className="mb-3 h-5 w-5" /><div className="text-sm font-semibold">空白模板</div><div className="mt-1 text-xs text-muted-foreground">新增产品，或为同批新产品填写多个价格区间。</div></button>
              <button className="rounded-md border bg-background p-5 text-left hover:border-primary/40" onClick={() => props.onDownload(true)}><FileSpreadsheet className="mb-3 h-5 w-5" /><div className="text-sm font-semibold">包含现有数据</div><div className="mt-1 text-xs text-muted-foreground">更新已有产品或精确修改已有价格区间。</div></button>
            </div>
            <div className="flex justify-between border-t pt-4"><Button variant="outline" onClick={() => props.onStep(1)}>上一步</Button><Button onClick={() => props.onStep(3)}>进入上传</Button></div>
          </>
        ) : step === 3 ? (
          <>
            <div><h2 className="text-base font-semibold">上传并程序检查</h2><p className="mt-1 text-xs text-muted-foreground">选择一个 .xlsx 文件；发现阻止项时整批不会写入。</p></div>
            <label className="flex min-h-36 cursor-pointer flex-col items-center justify-center rounded-md border border-dashed bg-background p-6 text-center">
              {busy ? <Loader2 className="h-6 w-6 animate-spin" /> : <FileSpreadsheet className="h-6 w-6 text-muted-foreground" />}
              <span className="mt-3 text-sm font-medium">{selectedFile?.name || "选择 Excel 文件"}</span>
              <input className="hidden" type="file" accept=".xlsx" disabled={busy} onChange={(event) => { const file = event.target.files?.[0]; if (file) props.onFile(file); }} />
            </label>
            {batch && <Summary batch={batch} />}
            {rows && <IssueList rows={rows} />}
            {rows && <Pagination page={rows.page} pages={rows.pages} busy={busy} onPage={props.onRowsPage} />}
            <div className="flex justify-between border-t pt-4"><Button variant="outline" onClick={() => props.onStep(2)}>上一步</Button><Button disabled={!batch?.can_commit || !rows || busy} onClick={() => props.onStep(4)}>核对变更</Button></div>
          </>
        ) : step === 4 && batch && rows ? (
          <>
            <div><h2 className="text-base font-semibold">核对变更</h2><p className="mt-1 text-xs text-muted-foreground">左侧是当前数据，右侧是导入后的数据；确认后整批一次写入。</p></div>
            <Summary batch={batch} /><Review rows={rows} />
            <Pagination page={rows.page} pages={rows.pages} busy={busy} onPage={props.onRowsPage} />
            <div className="flex justify-between border-t pt-4"><Button variant="outline" onClick={() => props.onStep(3)}>返回检查</Button><Button disabled={!batch.can_commit || busy} onClick={props.onCommit}>确认导入</Button></div>
          </>
        ) : (
          <>
            <div><h2 className="text-base font-semibold">导入结果</h2><p className="mt-1 text-xs text-muted-foreground">批次 {result?.batch_id || batch?.id} 已完成，请在下方产品列表核对。</p></div>
            {result && <div className="grid grid-cols-3 gap-px overflow-hidden rounded-md border bg-border">{[["新增", result.created], ["更新", result.updated], ["跳过", result.skipped]].map(([label, value]) => <div key={label} className="bg-background p-4"><div className="text-xs text-muted-foreground">{label}</div><div className="mt-1 text-lg font-semibold">{value}</div></div>)}</div>}
            <div className="flex justify-end border-t pt-4"><Button variant="outline" disabled={busy} onClick={props.onRollback}><RotateCcw className="mr-1.5 h-4 w-4" />回滚本批次</Button></div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export default function DatabaseSetupPage() {
  const router = useRouter();
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [products, setProducts] = useState<ProductPage | null>(null);
  const [query, setQuery] = useState("");
  const [step, setStep] = useState(1);
  const [batch, setBatch] = useState<ImportBatch | null>(null);
  const [rows, setRows] = useState<ImportRowsPage | null>(null);
  const [result, setResult] = useState<CommitResult | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const request = useRef(0);

  const loadProducts = async (q = query, page = 1) => {
    const id = ++request.current;
    try { const resultPage = await listSetupProducts(q, page); if (id === request.current) setProducts(resultPage); }
    catch { if (id === request.current) setProducts(null); }
  };
  useEffect(() => {
    let active = true;
    getDatabaseSetupStatus().then((next) => { if (active) setStatus(next); }).catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "无法读取准备状态"); });
    void loadProducts("");
    return () => { active = false; request.current += 1; };
  }, []);

  const saveBlob = async (includeExisting: boolean) => {
    setBusy(true); setError(null);
    try {
      const blob = await downloadProductTemplate(includeExisting);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = includeExisting ? "products-with-prices.xlsx" : "product-import-template.xlsx"; anchor.click();
      URL.revokeObjectURL(url);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "模板下载失败"); }
    finally { setBusy(false); }
  };
  const upload = async (nextFile: File) => {
    setFile(nextFile); setBusy(true); setError(null); setRows(null);
    try {
      const uploaded = await uploadDatabaseWorkbook(nextFile);
      const checked = await validateDatabaseImport(uploaded.id);
      setBatch(checked);
      setRows(await getDatabaseImportRows(uploaded.id));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "检查失败，文件仍保留"); }
    finally { setBusy(false); }
  };
  const commit = async () => {
    if (!batch) return;
    setBusy(true); setError(null);
    try { setResult(await commitImportWithRecovery(batch.id)); setStep(5); await loadProducts(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "无法确认提交结果"); }
    finally { setBusy(false); }
  };
  const loadRowsPage = async (page: number) => {
    if (!batch) return;
    setBusy(true); setError(null);
    try { setRows(await getDatabaseImportRows(batch.id, page)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取导入明细"); }
    finally { setBusy(false); }
  };
  const rollback = async () => {
    if (!batch) return;
    if (!window.confirm("确认回滚本批次？本批次创建的数据将归档，更新的数据将恢复到导入前。")) return;
    setBusy(true); setError(null);
    try { await rollbackDatabaseImport(batch.id); setBatch(null); setRows(null); setResult(null); setFile(null); setStep(1); await loadProducts(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "回滚失败"); }
    finally { setBusy(false); }
  };

  return (
    <div className="h-full overflow-y-auto bg-muted/20">
      <div className="mx-auto max-w-[1600px] px-6 py-6">
        <button className="mb-2 flex items-center gap-1 text-xs text-muted-foreground" onClick={() => router.push("/dashboard/workbench")}><ArrowLeft className="h-3.5 w-3.5" />返回工作台</button>
        <div className="mb-4"><h1 className="text-lg font-semibold">新数据库产品准备</h1><p className="mt-1 text-xs text-muted-foreground">配置字段后，通过一个 Excel 整批准备产品资料和多个价格区间。</p></div>
        {status ? <DatabaseSetupView step={step} status={status} batch={batch} rows={rows} result={result} busy={busy} error={error} selectedFile={file} onStep={setStep} onFile={(value) => void upload(value)} onDownload={(value) => void saveBlob(value)} onRowsPage={(value) => void loadRowsPage(value)} onCommit={() => void commit()} onRollback={() => void rollback()} /> : <Card><CardContent className="flex h-40 items-center justify-center text-sm text-muted-foreground">{error || "正在读取准备状态…"}</CardContent></Card>}
        <section className="mt-6 space-y-3">
          <div className="flex items-end justify-between gap-3"><div><h2 className="text-base font-semibold">产品数据</h2><p className="mt-1 text-xs text-muted-foreground">每个“产品代码 + 港口”一行；点击查看按时间排列的价格区间。</p></div><div className="flex gap-2"><Input className="h-8 w-64" value={query} placeholder="搜索产品代码或名称" onChange={(event) => setQuery(event.target.value)} /><Button size="sm" variant="outline" onClick={() => void loadProducts()}>查询</Button></div></div>
          <ProductTable products={products?.items || []} />
          {products && <Pagination page={products.page} pages={products.pages} busy={false} onPage={(page) => void loadProducts(query, page)} />}
        </section>
      </div>
    </div>
  );
}
