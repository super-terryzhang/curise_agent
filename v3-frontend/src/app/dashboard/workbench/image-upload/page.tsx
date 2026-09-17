"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  GripVertical,
  ImagePlus,
  Loader2,
  RefreshCw,
  Search,
  Star,
  UploadCloud,
  X,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  cancelBulkImageBatch,
  commitBulkImageBatch,
  createDirectImageBatch,
  getActiveDirectImageBatch,
  getBulkImageBatch,
  listDirectImageBatches,
  retryDirectImageRow,
  saveDirectImagePlan,
  updateDirectImageRow,
  uploadDirectImage,
  validateDirectImageBatch,
  type BulkImageBatch,
  type BulkImageStagingRow,
  type DirectImagePlan,
} from "@/lib/bulk-images-api";
import { listProducts, type ProductItem } from "@/lib/data-api";
import { buildDefaultImageOrder, makePrimary, moveImage } from "@/lib/image-upload-order";
import { cn } from "@/lib/utils";

const STEPS = ["选择图片", "程序检查", "主图与顺序", "确认提交"];
const ACCEPT = "image/jpeg,image/png,image/webp";

const ISSUE_LABELS: Record<string, string> = {
  product_required: "未指定产品",
  product_not_found: "产品不存在",
  unsupported_format: "格式不支持",
  file_too_large: "文件超过 5MB",
  empty_file: "空文件",
  invalid_image: "图片无法读取",
  capacity_exceeded: "超过图片上限",
  commit_failed: "写入失败",
};

function StepBar({ current }: { current: number }) {
  return (
    <div className="grid grid-cols-4 border-b bg-muted/20 px-5 py-4">
      {STEPS.map((label, index) => {
        const number = index + 1;
        const done = number < current;
        const active = number === current;
        return (
          <div key={label} className="flex items-center">
            <div className={cn(
              "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
              done && "border-foreground bg-foreground text-background",
              active && "border-primary bg-primary text-primary-foreground",
              !done && !active && "bg-background text-muted-foreground",
            )}>
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

function ProductLabel({ product }: { product: ProductItem }) {
  return (
    <>
      <span className="font-medium">{product.code || `#${product.id}`}</span>
      <span className="ml-2 text-muted-foreground">{product.product_name_en || product.product_name_jp || "未命名产品"}</span>
      <span className="ml-2 text-[11px] text-muted-foreground">{product.port_name || "未设置港口"} · 现有 {product.image_count} 张</span>
    </>
  );
}

function ResultSummary({ batch }: { batch: BulkImageBatch }) {
  return (
    <div className="grid grid-cols-4 gap-px overflow-hidden rounded-md border bg-border">
      {[
        ["图片总数", batch.total_files],
        ["已写入", batch.ingested_count],
        ["已排除", batch.excluded_count || 0],
        ["失败", batch.failed_count || 0],
      ].map(([label, value]) => (
        <div key={label} className="bg-background px-4 py-3">
          <div className="text-[11px] text-muted-foreground">{label}</div>
          <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
        </div>
      ))}
    </div>
  );
}

export default function ProductImageUploadPage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [step, setStep] = useState(1);
  const [batch, setBatch] = useState<BulkImageBatch | null>(null);
  const [history, setHistory] = useState<BulkImageBatch[]>([]);
  const [products, setProducts] = useState<ProductItem[]>([]);
  const [productSearch, setProductSearch] = useState("");
  const [selectedProductId, setSelectedProductId] = useState<number | null>(null);
  const [selectedPlanId, setSelectedPlanId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [draggingFiles, setDraggingFiles] = useState(false);
  const [uploadProgress, setUploadProgress] = useState({ done: 0, total: 0 });
  const [dragToken, setDragToken] = useState<string | null>(null);

  const loadProducts = useCallback(async (search = "") => {
    const result = await listProducts({ search: search || undefined, limit: 100, is_effective: true });
    setProducts(result.items);
    if (!selectedProductId && result.items.length > 0) setSelectedProductId(result.items[0].id);
  }, [selectedProductId]);

  const refreshHistory = useCallback(async () => {
    setHistory((await listDirectImageBatches()).items);
  }, []);

  useEffect(() => {
    let alive = true;
    Promise.all([getActiveDirectImageBatch(), listProducts({ limit: 100, is_effective: true }), listDirectImageBatches()])
      .then(([active, productPage, batches]) => {
        if (!alive) return;
        setProducts(productPage.items);
        setSelectedProductId(productPage.items[0]?.id ?? null);
        setHistory(batches.items);
        if (active) {
          setBatch(active);
          setStep(active.status === "preview_ready" ? 2 : 1);
        }
      })
      .catch((error) => toast.error(error instanceof Error ? error.message : "页面读取失败"));
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadProducts(productSearch).catch((error) => toast.error(error instanceof Error ? error.message : "产品读取失败"));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [productSearch, loadProducts]);

  const ensureBatch = useCallback(async () => {
    if (batch && ["uploading", "preview_ready"].includes(batch.status)) return batch;
    const created = await createDirectImageBatch();
    setBatch(created);
    return created;
  }, [batch]);

  const handleFiles = useCallback(async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (list.length === 0) return;
    if (!selectedProductId) {
      toast.error("请先选择这些图片属于哪个产品");
      return;
    }
    setBusy(true);
    setUploadProgress({ done: 0, total: list.length });
    try {
      const current = await ensureBatch();
      let failures = 0;
      for (let index = 0; index < list.length; index += 1) {
        try {
          await uploadDirectImage(current.id, list[index], selectedProductId);
        } catch (error) {
          failures += 1;
          toast.error(`${list[index].name}：${error instanceof Error ? error.message : "上传失败"}`);
        }
        setUploadProgress({ done: index + 1, total: list.length });
      }
      setBatch(await getBulkImageBatch(current.id));
      if (failures === 0) toast.success(`已暂存 ${list.length} 张图片`);
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }, [ensureBatch, selectedProductId]);

  const checkBatch = useCallback(async () => {
    if (!batch || batch.total_files === 0) return;
    setBusy(true);
    try {
      const checked = await validateDirectImageBatch(batch.id);
      setBatch(checked);
      setStep(2);
      if (checked.error_count === 0) toast.success("全部图片通过检查");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "检查失败");
    } finally {
      setBusy(false);
    }
  }, [batch]);

  const changeRow = useCallback(async (
    row: BulkImageStagingRow,
    body: { product_id?: number; decision?: "include" | "exclude" },
  ) => {
    if (!batch) return;
    setBusy(true);
    try {
      setBatch(await updateDirectImageRow(batch.id, row.id, body));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新失败");
    } finally {
      setBusy(false);
    }
  }, [batch]);

  const goToOrder = useCallback(() => {
    if (!batch?.can_continue) return;
    setSelectedPlanId(batch.plans?.[0]?.product_id ?? null);
    setStep(3);
  }, [batch]);

  const selectedPlan = useMemo(
    () => batch?.plans?.find((plan) => plan.product_id === selectedPlanId) ?? batch?.plans?.[0] ?? null,
    [batch, selectedPlanId],
  );

  const stagedById = useMemo(
    () => new Map((batch?.rows || []).map((row) => [row.id, row])),
    [batch],
  );

  const saveOrder = useCallback(async (plan: DirectImagePlan, items: string[]) => {
    if (!batch) return;
    try {
      const saved = await saveDirectImagePlan(batch.id, plan.product_id, items);
      setBatch({
        ...batch,
        plans: (batch.plans || []).map((item) => item.product_id === saved.product_id ? saved : item),
      });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "顺序保存失败");
    }
  }, [batch]);

  const reorder = useCallback((fromToken: string, toToken: string) => {
    if (!selectedPlan || fromToken === toToken) return;
    const from = selectedPlan.ordered_items.indexOf(fromToken);
    const to = selectedPlan.ordered_items.indexOf(toToken);
    void saveOrder(selectedPlan, moveImage(selectedPlan.ordered_items, from, to));
  }, [saveOrder, selectedPlan]);

  const resetPlan = useCallback(() => {
    if (!selectedPlan || !batch) return;
    const staged = batch.rows.filter(
      (row) => row.product_id === selectedPlan.product_id && row.decision !== "exclude" && row.status === "ready",
    ).map((row) => ({ id: row.id, upload_order: row.upload_order || row.id }));
    const items = buildDefaultImageOrder(selectedPlan.existing_images, staged).map((item) => item.token);
    void saveOrder(selectedPlan, items);
  }, [batch, saveOrder, selectedPlan]);

  const commit = useCallback(async () => {
    if (!batch) return;
    setBusy(true);
    try {
      let current = await commitBulkImageBatch(batch.id);
      setBatch(current);
      for (let attempt = 0; attempt < 120 && current.status === "processing"; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        current = await getBulkImageBatch(batch.id);
        setBatch(current);
      }
      if (current.status === "completed") {
        toast.success("产品图片已更新");
        await refreshHistory();
      } else if (current.status === "error") {
        toast.error(current.error_message || "提交失败");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "提交失败");
    } finally {
      setBusy(false);
    }
  }, [batch, refreshHistory]);

  const cancel = useCallback(async () => {
    if (!batch) return;
    setBusy(true);
    try {
      await cancelBulkImageBatch(batch.id, { force: batch.status === "processing" });
      setBatch(null);
      setStep(1);
      await refreshHistory();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "取消失败");
    } finally {
      setBusy(false);
    }
  }, [batch, refreshHistory]);

  const issues = batch?.rows.filter((row) => row.status === "needs_attention") || [];

  return (
    <div className="h-full overflow-y-auto bg-muted/20">
      <div className="mx-auto max-w-7xl px-6 py-7">
        <div className="mb-5 flex items-start justify-between gap-4">
          <PageHeader title="产品图片上传" description="上传、检查并核对最终图库；确认前不会修改正式产品图片。" />
          <Button variant="outline" size="sm" onClick={() => router.push("/dashboard/workbench")}><ArrowLeft className="mr-1 h-4 w-4" />返回工作台</Button>
        </div>

        <Card className="overflow-hidden border-border/80 shadow-sm">
          <StepBar current={step} />
          <CardContent className="p-5">
            {step === 1 && (
              <div className="space-y-5">
                <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
                  <section className="rounded-md border bg-background p-4">
                    <label className="text-xs font-semibold">1. 选择图片对应的产品</label>
                    <div className="relative mt-3">
                      <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                      <input value={productSearch} onChange={(event) => setProductSearch(event.target.value)} placeholder="产品代码或名称" className="h-9 w-full rounded-md border bg-background pl-9 pr-3 text-sm outline-none focus:border-primary" />
                    </div>
                    <div className="mt-3 max-h-72 divide-y overflow-y-auto rounded-md border">
                      {products.map((product) => (
                        <button key={product.id} type="button" onClick={() => setSelectedProductId(product.id)} className={cn("block w-full px-3 py-3 text-left text-xs hover:bg-muted/50", selectedProductId === product.id && "bg-primary/5 ring-1 ring-inset ring-primary/30")}>
                          <ProductLabel product={product} />
                        </button>
                      ))}
                      {products.length === 0 && <p className="p-4 text-center text-xs text-muted-foreground">没有找到产品</p>}
                    </div>
                  </section>

                  <section className="rounded-md border bg-background p-4">
                    <div className="text-xs font-semibold">2. 添加这个产品的图片</div>
                    <div
                      className={cn("mt-3 flex min-h-56 flex-col items-center justify-center rounded-md border-2 border-dashed px-6 text-center transition-colors", draggingFiles && "border-primary bg-primary/5", busy && "pointer-events-none opacity-60")}
                      onDragOver={(event) => { event.preventDefault(); setDraggingFiles(true); }}
                      onDragLeave={() => setDraggingFiles(false)}
                      onDrop={(event) => { event.preventDefault(); setDraggingFiles(false); void handleFiles(event.dataTransfer.files); }}
                    >
                      <UploadCloud className="h-8 w-8 text-muted-foreground" />
                      <p className="mt-3 text-sm font-medium">拖入多张图片，或点击选择</p>
                      <p className="mt-1 text-xs text-muted-foreground">JPG / PNG / WebP，单张不超过 5MB，最多 30 张/产品</p>
                      <Button className="mt-4" variant="outline" disabled={!selectedProductId || busy} onClick={() => fileRef.current?.click()}><ImagePlus className="mr-1 h-4 w-4" />选择图片</Button>
                      <input ref={fileRef} className="hidden" type="file" accept={ACCEPT} multiple onChange={(event) => event.target.files && void handleFiles(event.target.files)} />
                      {busy && uploadProgress.total > 0 && <p className="mt-3 flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />正在暂存 {uploadProgress.done}/{uploadProgress.total}</p>}
                    </div>
                  </section>
                </div>

                {batch && batch.total_files > 0 && (
                  <div className="rounded-md border bg-background">
                    <div className="flex items-center justify-between border-b px-4 py-3"><div><div className="text-sm font-semibold">本批次已添加 {batch.total_files} 张</div><div className="mt-1 text-[11px] text-muted-foreground">图片仍在暂存区，尚未修改产品图库</div></div><Button onClick={() => void checkBatch()} disabled={busy}>运行程序检查<ArrowRight className="ml-1 h-4 w-4" /></Button></div>
                    <div className="grid grid-cols-3 gap-3 p-4 sm:grid-cols-6 lg:grid-cols-10">
                      {batch.rows.map((row) => <div key={row.id} className="relative aspect-square overflow-hidden rounded border bg-muted/30">{row.preview_url ? <img src={row.preview_url} alt={row.image_filename} className="h-full w-full object-cover" /> : <AlertCircle className="absolute inset-0 m-auto h-5 w-5 text-destructive" />}</div>)}
                    </div>
                  </div>
                )}

                {history.length > 0 && (
                  <section><div className="mb-2 text-xs font-semibold">最近上传</div><div className="divide-y overflow-hidden rounded-md border bg-background">{history.slice(0, 5).map((item) => <div key={item.id} className="grid grid-cols-[1fr_auto_auto] items-center gap-4 px-4 py-3 text-xs"><div><div className="font-medium">{item.zip_filename}</div><div className="mt-1 text-muted-foreground">{item.created_at ? new Date(item.created_at).toLocaleString("zh-CN") : "—"}</div></div><Badge variant="outline">{item.status === "completed" ? "已完成" : item.status === "cancelled" ? "已取消" : item.status}</Badge><span className="tabular-nums text-muted-foreground">{item.ingested_count}/{item.total_files}</span></div>)}</div></section>
                )}
              </div>
            )}

            {step === 2 && batch && (
              <div className="space-y-4">
                <ResultSummary batch={batch} />
                {issues.length === 0 ? (
                  <div className="flex min-h-52 flex-col items-center justify-center rounded-md border bg-background text-center"><CheckCircle2 className="h-8 w-8 text-emerald-600" /><h2 className="mt-3 text-sm font-semibold">全部图片通过检查</h2><p className="mt-1 text-xs text-muted-foreground">下一步核对每个产品的主图和最终显示顺序。</p></div>
                ) : (
                  <div className="overflow-hidden rounded-md border bg-background">
                    <div className="flex items-center justify-between gap-4 border-b px-4 py-3"><div><h2 className="text-sm font-semibold">需要处理 {issues.length} 项</h2><p className="mt-1 text-xs text-muted-foreground">每项必须重新分配产品、返回替换，或明确排除。</p></div><div className="relative w-64"><Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" /><input value={productSearch} onChange={(event) => setProductSearch(event.target.value)} placeholder="搜索要分配的产品" className="h-8 w-full rounded-md border bg-background pl-8 pr-2 text-xs outline-none focus:border-primary" /></div></div>
                    <div className="divide-y">{issues.map((row) => (
                      <div key={row.id} className="grid gap-3 px-4 py-3 md:grid-cols-[56px_1fr_260px_auto] md:items-center">
                        <div className="h-12 w-12 overflow-hidden rounded border bg-muted/30">{row.preview_url ? <img src={row.preview_url} alt="" className="h-full w-full object-cover" /> : <AlertCircle className="m-3 h-5 w-5 text-destructive" />}</div>
                        <div><div className="text-xs font-medium">{row.image_filename}</div><div className="mt-1 text-[11px] text-destructive">{ISSUE_LABELS[row.issue_code || ""] || "检查未通过"}：{row.error_message}</div></div>
                        <select value={row.product_id || ""} onChange={(event) => event.target.value && void changeRow(row, { product_id: Number(event.target.value) })} className="h-9 rounded-md border bg-background px-2 text-xs"><option value="">选择产品</option>{products.map((product) => <option key={product.id} value={product.id}>{product.code || product.id} · {product.product_name_en || product.product_name_jp}</option>)}</select>
                        <Button variant="outline" size="sm" onClick={() => void changeRow(row, { decision: "exclude" })}><X className="mr-1 h-3.5 w-3.5" />排除</Button>
                      </div>
                    ))}</div>
                  </div>
                )}
                <div className="flex justify-between"><Button variant="outline" onClick={() => setStep(1)}><ArrowLeft className="mr-1 h-4 w-4" />返回补充图片</Button><Button disabled={!batch.can_continue || busy} onClick={goToOrder}>核对主图和顺序<ArrowRight className="ml-1 h-4 w-4" /></Button></div>
              </div>
            )}

            {step === 3 && batch && selectedPlan && (
              <div className="space-y-4">
                <div className="grid min-h-[500px] overflow-hidden rounded-md border bg-background lg:grid-cols-[280px_1fr]">
                  <aside className="border-r bg-muted/10"><div className="border-b px-4 py-3"><div className="text-xs font-semibold">本批次产品</div><div className="mt-1 text-[11px] text-muted-foreground">选择产品查看最终图库</div></div><div className="divide-y">{(batch.plans || []).map((plan) => { const added = plan.ordered_items.filter((item) => item.startsWith("staged:")); return <button key={plan.product_id} type="button" onClick={() => setSelectedPlanId(plan.product_id)} className={cn("block w-full px-4 py-3 text-left", selectedPlan.product_id === plan.product_id && "bg-primary/5 border-l-2 border-primary")}><div className="text-xs font-semibold">{plan.product_code || `#${plan.product_id}`}</div><div className="mt-1 truncate text-xs text-muted-foreground">{plan.product_name || "未命名产品"}</div><div className="mt-2 text-[11px] text-muted-foreground">现有 {plan.existing_images.length} · 新增 {added.length} · 最终 {plan.ordered_items.length}</div></button>; })}</div></aside>
                  <section className="p-4"><div className="flex items-start justify-between gap-4"><div><h2 className="text-sm font-semibold">{selectedPlan.product_code} · {selectedPlan.product_name}</h2><p className="mt-1 text-xs text-muted-foreground">拖动图片调整最终顺序；第一张就是主图。所有修改在最后确认前都不会写入正式图库。</p></div><Button variant="outline" size="sm" onClick={resetPlan}><RefreshCw className="mr-1 h-3.5 w-3.5" />恢复默认</Button></div>
                    <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">{selectedPlan.ordered_items.map((token, index) => { const [kind, rawId] = token.split(":"); const existing = kind === "existing" ? selectedPlan.existing_images.find((image) => image.id === Number(rawId)) : null; const staged = kind === "staged" ? stagedById.get(Number(rawId)) : null; const preview = existing?.preview_url || staged?.preview_url; return <div key={token} draggable onDragStart={() => setDragToken(token)} onDragOver={(event) => event.preventDefault()} onDrop={() => { if (dragToken) reorder(dragToken, token); setDragToken(null); }} className={cn("group overflow-hidden rounded-md border bg-background", index === 0 && "ring-2 ring-primary/50")}><div className="relative aspect-square bg-muted/30">{preview ? <img src={preview} alt={existing?.filename || staged?.image_filename || "产品图片"} className="h-full w-full object-cover" /> : <ImagePlus className="absolute inset-0 m-auto h-6 w-6 text-muted-foreground" />}<span className="absolute left-1.5 top-1.5 rounded bg-background/90 px-1.5 py-0.5 text-[10px] font-medium">{index === 0 ? "主图" : `${index + 1}`}</span>{kind === "staged" && <span className="absolute right-1.5 top-1.5 rounded bg-blue-600 px-1.5 py-0.5 text-[9px] text-white">新增</span>}<GripVertical className="absolute bottom-1.5 right-1.5 h-4 w-4 text-white drop-shadow" /></div><div className="flex items-center justify-between gap-1 border-t p-1.5"><Button variant="ghost" size="sm" className="h-7 px-2 text-[10px]" disabled={index === 0} onClick={() => void saveOrder(selectedPlan, makePrimary(selectedPlan.ordered_items.map((item) => ({ token: item })), token).map((item) => item.token))}><Star className="mr-1 h-3 w-3" />设为主图</Button><div className="flex"><Button aria-label="左移" variant="ghost" size="icon" className="h-7 w-7" disabled={index === 0} onClick={() => void saveOrder(selectedPlan, moveImage(selectedPlan.ordered_items, index, index - 1))}><ArrowLeft className="h-3 w-3" /></Button><Button aria-label="右移" variant="ghost" size="icon" className="h-7 w-7" disabled={index === selectedPlan.ordered_items.length - 1} onClick={() => void saveOrder(selectedPlan, moveImage(selectedPlan.ordered_items, index, index + 1))}><ArrowRight className="h-3 w-3" /></Button></div></div></div>; })}</div>
                  </section>
                </div>
                <div className="flex justify-between"><Button variant="outline" onClick={() => setStep(2)}><ArrowLeft className="mr-1 h-4 w-4" />返回检查</Button><Button onClick={() => setStep(4)}>确认本次更新<ArrowRight className="ml-1 h-4 w-4" /></Button></div>
              </div>
            )}

            {step === 4 && batch && (
              <div className="space-y-5">
                <ResultSummary batch={batch} />
                {batch.status === "completed" ? (
                  <div className="rounded-md border bg-background p-8 text-center"><CheckCircle2 className="mx-auto h-9 w-9 text-emerald-600" /><h2 className="mt-3 text-base font-semibold">图片更新完成</h2><p className="mt-2 text-sm text-muted-foreground">成功写入 {batch.ingested_count} 张，失败 {batch.failed_count || 0} 张。主图与显示顺序已经按核对结果保存。</p>{(batch.failed_count || 0) > 0 && <div className="mx-auto mt-5 max-w-2xl divide-y overflow-hidden rounded-md border text-left">{batch.rows.filter((row) => row.status === "committed_failed").map((row) => <div key={row.id} className="flex items-center justify-between gap-3 px-4 py-3 text-xs"><div><div className="font-medium">{row.image_filename}</div><div className="mt-1 text-destructive">{row.error_message}</div></div><Button size="sm" variant="outline" onClick={async () => { await retryDirectImageRow(batch.id, row.id); setBatch(await getBulkImageBatch(batch.id)); }}>重试</Button></div>)}</div>}<div className="mt-6 flex justify-center gap-2"><Button variant="outline" onClick={() => router.push("/dashboard/data")}>查看产品图库</Button><Button onClick={() => { setBatch(null); setStep(1); setUploadProgress({ done: 0, total: 0 }); }}>开始新批次</Button></div></div>
                ) : (
                  <div className="rounded-md border bg-background p-5"><h2 className="text-sm font-semibold">提交前确认</h2><div className="mt-4 divide-y rounded-md border">{(batch.plans || []).map((plan) => <div key={plan.product_id} className="grid grid-cols-[1fr_auto] gap-4 px-4 py-3 text-xs"><div><span className="font-semibold">{plan.product_code}</span><span className="ml-2 text-muted-foreground">{plan.product_name}</span></div><div className="text-muted-foreground">现有 {plan.existing_images.length} · 新增 {plan.ordered_items.filter((item) => item.startsWith("staged:")).length} · 最终 {plan.ordered_items.length}</div></div>)}</div>{batch.error_message && <div className="mt-4 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">{batch.error_message}</div>}<div className="mt-5 flex justify-between"><Button variant="outline" disabled={busy} onClick={() => setStep(3)}><ArrowLeft className="mr-1 h-4 w-4" />返回调整</Button><Button disabled={busy || batch.status === "processing"} onClick={() => void commit()}>{busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Check className="mr-1 h-4 w-4" />}确认并更新产品图库</Button></div></div>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        {batch && !["completed", "cancelled"].includes(batch.status) && <div className="mt-3 flex justify-end"><Button variant="ghost" size="sm" className="text-muted-foreground" disabled={busy} onClick={() => void cancel()}>取消并清理本批次</Button></div>}
      </div>
    </div>
  );
}
