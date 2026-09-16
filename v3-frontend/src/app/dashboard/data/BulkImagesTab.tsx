"use client";

/**
 * Bulk image upload — 3-step wizard inside the 数据管理 page (2026-06-22).
 *
 * Step 1: Filter products → download empty ZIP template.
 *         User fills folders with images on their machine, re-zips.
 * Step 2: Upload filled ZIP → preview shows matched / unmatched / error rows.
 * Step 3: Commit → backend ingests via add_product_image; UI polls
 *         batch status until completed.
 *
 * Designed to mirror v2 data-upload UX patterns (Step 1 → 2 → 3 with
 * inline preview), so users have one consistent mental model across
 * the two bulk-upload surfaces.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  Download,
  Loader2,
  Upload,
  CheckCircle2,
  AlertCircle,
  XCircle,
  ArrowRight,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import {
  cancelBulkImageBatch,
  commitBulkImageBatch,
  downloadTemplate,
  getBulkImageBatch,
  uploadBulkImageZipWithProgress,
  type BulkImageBatch,
} from "@/lib/bulk-images-api";
import {
  listCountries,
  listPorts,
  type CountryItem,
  type PortItem,
} from "@/lib/data-api";

type Step = 1 | 2 | 3;

export default function BulkImagesTab() {
  const [step, setStep] = useState<Step>(1);

  // Filter state for template download.
  const [countries, setCountries] = useState<CountryItem[]>([]);
  const [ports, setPorts] = useState<PortItem[]>([]);
  const [selectedCountryIds, setSelectedCountryIds] = useState<Set<number>>(new Set());
  const [selectedPortIds, setSelectedPortIds] = useState<Set<number>>(new Set());
  const [onlyMissing, setOnlyMissing] = useState(false);
  const [downloading, setDownloading] = useState(false);

  // Upload state.
  const [uploading, setUploading] = useState(false);
  const [uploadedBytes, setUploadedBytes] = useState(0);
  const [uploadTotalBytes, setUploadTotalBytes] = useState(0);
  const [uploadElapsedSec, setUploadElapsedSec] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Active batch (preview or processing).
  const [batch, setBatch] = useState<BulkImageBatch | null>(null);
  const [committing, setCommitting] = useState(false);
  // Track how long the batch has been in `processing` — if it exceeds
  // 15 min without progress the worker likely died (Cloud Run CPU
  // throttled OR process restart), and we expose a "force cancel"
  // button so the user isn't stuck.
  const processingStartedAtRef = useRef<number | null>(null);
  const [processingElapsedSec, setProcessingElapsedSec] = useState(0);

  useEffect(() => {
    listCountries().then(setCountries).catch(() => {});
    listPorts().then(setPorts).catch(() => {});
  }, []);

  // Ports visible to the user — when one or more countries are
  // selected, restrict the port chips to that country's ports. Empty
  // country selection = show all ports (no filter applied).
  const visiblePorts =
    selectedCountryIds.size === 0
      ? ports
      : ports.filter(
          (p) => p.country_id != null && selectedCountryIds.has(p.country_id),
        );

  // When the country selection changes, prune any selected port that
  // no longer maps to a visible country. Without this, a user who
  // picks port X (in country A), then switches countries to B, would
  // silently leave port X selected — the backend would then receive a
  // port_id that contradicts the country filter and return an empty
  // template. Guard against silent state desync.
  useEffect(() => {
    if (selectedCountryIds.size === 0) return;
    const allowed = new Set(visiblePorts.map((p) => p.id));
    let needsPrune = false;
    for (const id of selectedPortIds) {
      if (!allowed.has(id)) {
        needsPrune = true;
        break;
      }
    }
    if (needsPrune) {
      setSelectedPortIds((prev) => {
        const next = new Set<number>();
        for (const id of prev) if (allowed.has(id)) next.add(id);
        return next;
      });
    }
    // visiblePorts is derived from ports + selectedCountryIds — listing
    // them as deps is correct here and won't loop because the prune
    // call only sets state when the set actually shrinks.
  }, [selectedCountryIds, visiblePorts, selectedPortIds]);

  // Step 3: poll status while processing.
  useEffect(() => {
    if (!batch || batch.status !== "processing") return;
    const id = setInterval(async () => {
      try {
        const updated = await getBulkImageBatch(batch.id);
        setBatch(updated);
        if (updated.status === "completed" || updated.status === "error") {
          clearInterval(id);
        }
      } catch {
        // transient — keep polling
      }
    }, 2000);
    return () => clearInterval(id);
  }, [batch]);

  // Elapsed-time counter while processing — feeds the 15-minute stall
  // detector. Resets to 0 whenever the batch id changes OR the status
  // leaves `processing`. Uses a ref for the start-time so the ticker
  // effect doesn't rebuild every re-render.
  useEffect(() => {
    if (!batch || batch.status !== "processing") {
      processingStartedAtRef.current = null;
      setProcessingElapsedSec(0);
      return;
    }
    if (!processingStartedAtRef.current) {
      processingStartedAtRef.current = Date.now();
    }
    const tick = () => {
      const start = processingStartedAtRef.current;
      if (start) setProcessingElapsedSec(Math.floor((Date.now() - start) / 1000));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [batch]);

  // ─── Step 1: Template download ─────────────────────────

  const handleDownload = useCallback(async () => {
    setDownloading(true);
    try {
      const blob = await downloadTemplate({
        countryIds: selectedCountryIds.size > 0 ? [...selectedCountryIds] : undefined,
        portIds: selectedPortIds.size > 0 ? [...selectedPortIds] : undefined,
        onlyMissingImages: onlyMissing,
      });
      // Trigger download via blob URL — keeps the auth header on the fetch.
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "product-images-template.zip";
      a.click();
      URL.revokeObjectURL(url);
      toast.success("模板已下载，请把图片放进对应文件夹后重新压成 ZIP 上传");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "下载失败");
    } finally {
      setDownloading(false);
    }
  }, [selectedCountryIds, selectedPortIds, onlyMissing]);

  // ─── Step 2: ZIP upload ───────────────────────────────

  const handleUpload = useCallback(
    async (file: File) => {
      if (!file.name.toLowerCase().endsWith(".zip")) {
        toast.error("请上传 .zip 文件");
        return;
      }
      if (file.size > 100 * 1024 * 1024) {
        toast.error("ZIP 超过 100MB 上限，请拆分后分批");
        return;
      }
      setUploading(true);
      setUploadedBytes(0);
      setUploadTotalBytes(file.size);
      setUploadElapsedSec(0);
      // Elapsed-time counter so the user sees "已上传 3 分 20 秒"
      // and knows the app is alive during slow uplinks.
      const startedAt = Date.now();
      const timer = setInterval(() => {
        setUploadElapsedSec(Math.floor((Date.now() - startedAt) / 1000));
      }, 1000);
      try {
        const result = await uploadBulkImageZipWithProgress(
          file,
          (loaded, total) => {
            setUploadedBytes(loaded);
            if (total > 0) setUploadTotalBytes(total);
          },
        );
        setBatch(result);
        setStep(2);
        if (result.status === "error") {
          toast.error(result.error_message || "解析失败");
        }
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "上传失败");
      } finally {
        clearInterval(timer);
        setUploading(false);
      }
    },
    [],
  );

  // ─── Step 3: Commit ───────────────────────────────────

  const handleCommit = useCallback(async () => {
    if (!batch) return;
    setCommitting(true);
    try {
      const updated = await commitBulkImageBatch(batch.id);
      setBatch(updated);
      setStep(3);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "提交失败");
    } finally {
      setCommitting(false);
    }
  }, [batch]);

  const handleCancel = useCallback(
    async (force = false) => {
      if (!batch) return;
      try {
        await cancelBulkImageBatch(batch.id, { force });
        setBatch(null);
        setStep(1);
        toast.info(
          force ? "已强制取消卡住的任务，可重新上传" : "已取消，可重新上传",
        );
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "取消失败");
      }
    },
    [batch],
  );

  // ─── Render ───────────────────────────────────────────

  return (
    <div className="h-full overflow-y-auto px-2">
      <StepIndicator step={step} />

      {step === 1 && (
        <Step1Template
          countries={countries}
          ports={visiblePorts}
          countryFilterActive={selectedCountryIds.size > 0}
          selectedCountryIds={selectedCountryIds}
          selectedPortIds={selectedPortIds}
          onlyMissing={onlyMissing}
          downloading={downloading}
          uploading={uploading}
          uploadedBytes={uploadedBytes}
          uploadTotalBytes={uploadTotalBytes}
          uploadElapsedSec={uploadElapsedSec}
          fileInputRef={fileInputRef}
          onToggleCountry={(id) => toggleSet(setSelectedCountryIds, id)}
          onTogglePort={(id) => toggleSet(setSelectedPortIds, id)}
          onToggleOnlyMissing={setOnlyMissing}
          onDownload={handleDownload}
          onUploadClick={() => fileInputRef.current?.click()}
          onFileChange={(f) => handleUpload(f)}
        />
      )}

      {step === 2 && batch && (
        <Step2Preview
          batch={batch}
          committing={committing}
          onCommit={handleCommit}
          onCancel={() => handleCancel(false)}
        />
      )}

      {step === 3 && batch && (
        <Step3Progress
          batch={batch}
          processingElapsedSec={processingElapsedSec}
          onReset={() => {
            setBatch(null);
            setStep(1);
          }}
          onForceCancel={() => handleCancel(true)}
        />
      )}
    </div>
  );
}

function toggleSet(
  setter: React.Dispatch<React.SetStateAction<Set<number>>>,
  id: number,
) {
  setter((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });
}

// ─── Step indicator (3 chips) ─────────────────────────────────

function StepIndicator({ step }: { step: Step }) {
  const items = [
    { n: 1, label: "下载模板" },
    { n: 2, label: "上传 + 预览" },
    { n: 3, label: "提交入库" },
  ];
  return (
    <div className="mb-6 flex items-center gap-2 text-xs">
      {items.map((it, i) => (
        <div key={it.n} className="flex items-center gap-2">
          <div
            className={cn(
              "flex h-6 w-6 items-center justify-center rounded-full border text-[11px] font-medium",
              step === it.n
                ? "border-primary bg-primary text-primary-foreground"
                : step > it.n
                ? "border-green-500 bg-green-500 text-white"
                : "border-border text-muted-foreground",
            )}
          >
            {step > it.n ? "✓" : it.n}
          </div>
          <span
            className={cn(
              "font-medium",
              step === it.n ? "text-foreground" : "text-muted-foreground",
            )}
          >
            {it.label}
          </span>
          {i < items.length - 1 && (
            <ArrowRight className="h-3 w-3 text-muted-foreground" />
          )}
        </div>
      ))}
    </div>
  );
}

// ─── Step 1: Template download + filter ──────────────────────

function Step1Template({
  countries,
  ports,
  countryFilterActive,
  selectedCountryIds,
  selectedPortIds,
  onlyMissing,
  downloading,
  uploading,
  uploadedBytes,
  uploadTotalBytes,
  uploadElapsedSec,
  fileInputRef,
  onToggleCountry,
  onTogglePort,
  onToggleOnlyMissing,
  onDownload,
  onUploadClick,
  onFileChange,
}: {
  countries: CountryItem[];
  ports: PortItem[];
  countryFilterActive: boolean;
  selectedCountryIds: Set<number>;
  selectedPortIds: Set<number>;
  onlyMissing: boolean;
  downloading: boolean;
  uploading: boolean;
  uploadedBytes: number;
  uploadTotalBytes: number;
  uploadElapsedSec: number;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  onToggleCountry: (id: number) => void;
  onTogglePort: (id: number) => void;
  onToggleOnlyMissing: (v: boolean) => void;
  onDownload: () => void;
  onUploadClick: () => void;
  onFileChange: (file: File) => void;
}) {
  return (
    <div className="space-y-6">
      {/* Download template card */}
      <div className="rounded-lg border border-border bg-card p-5">
        <h3 className="mb-1 text-sm font-semibold">第一步 · 下载模板</h3>
        <p className="mb-4 text-xs text-muted-foreground">
          模板按「国家 / 港口 / 产品代码 — 产品名」三层目录组织（例如{" "}
          <code className="rounded bg-muted px-1">BEEF-001 — Beef Tenderloin</code>）。
          下载后把图片拖到对应产品文件夹里，再把整个{" "}
          <code className="rounded bg-muted px-1">products/</code> 目录重新压成 ZIP 上传。
          文件夹名末尾的 ✓ 表示该产品<strong>已有图片</strong>；空文件夹不会触发更新。
        </p>

        <div className="space-y-4">
          <FilterBlock
            title="按国家筛选"
            items={countries.map((c) => ({ id: c.id, name: c.name }))}
            selected={selectedCountryIds}
            onToggle={onToggleCountry}
          />
          <FilterBlock
            title={
              countryFilterActive
                ? "按港口筛选（已限定为所选国家的港口）"
                : "按港口筛选"
            }
            items={ports.map((p) => ({ id: p.id, name: p.name }))}
            selected={selectedPortIds}
            onToggle={onTogglePort}
            emptyMessage={
              countryFilterActive
                ? "所选国家下没有港口"
                : "加载中…"
            }
          />
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              className="h-3.5 w-3.5"
              checked={onlyMissing}
              onChange={(e) => onToggleOnlyMissing(e.target.checked)}
            />
            <span>只下载暂无图片的产品（推荐用于补图）</span>
          </label>
        </div>

        <div className="mt-5 flex justify-end">
          <Button onClick={onDownload} disabled={downloading} size="sm">
            {downloading ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Download className="mr-2 h-4 w-4" />
            )}
            下载 ZIP 模板
          </Button>
        </div>
      </div>

      {/* Upload card */}
      <div className="rounded-lg border border-border bg-card p-5">
        <h3 className="mb-1 text-sm font-semibold">第二步 · 上传 ZIP</h3>
        <p className="mb-4 text-xs text-muted-foreground">
          填好图片后，把 <code className="rounded bg-muted px-1">products/</code> 文件夹打包成 ZIP 上传。
          单文件 ≤ 100 MB，单张图 ≤ 5 MB（支持 jpg / png / webp）。
        </p>
        <input
          ref={fileInputRef}
          type="file"
          accept=".zip"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) {
              onFileChange(f);
              e.target.value = "";
            }
          }}
        />
        <Button onClick={onUploadClick} disabled={uploading} size="sm">
          {uploading ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Upload className="mr-2 h-4 w-4" />
          )}
          选择 ZIP 文件
        </Button>

        {/* Live upload progress — visible only while a request is in
            flight. Slow uplinks + 100MB files can be 5+ min; showing
            bytes/elapsed prevents the user from thinking the page hung
            and starting a duplicate upload. */}
        {uploading && uploadTotalBytes > 0 && (
          <div className="mt-4 space-y-2 rounded-md border border-border/60 bg-muted/20 p-3">
            <div className="flex justify-between text-[11px] text-muted-foreground">
              <span>
                已上传 {_fmtBytes(uploadedBytes)} / {_fmtBytes(uploadTotalBytes)}
                {" · "}
                {_fmtElapsed(uploadElapsedSec)}
              </span>
              <span className="font-medium text-foreground">
                {Math.round((uploadedBytes / uploadTotalBytes) * 100)}%
              </span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-primary transition-all"
                style={{
                  width: `${Math.round((uploadedBytes / uploadTotalBytes) * 100)}%`,
                }}
              />
            </div>
            {uploadElapsedSec > 60 && (
              <p className="text-[11px] text-amber-600">
                文件较大，请勿关闭页面 — 关闭会中断上传，需要重新开始。
              </p>
            )}
            {uploadedBytes >= uploadTotalBytes && uploadTotalBytes > 0 && (
              <p className="text-[11px] text-muted-foreground">
                上传完成，服务端正在解析 ZIP…
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function _fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function _fmtElapsed(sec: number): string {
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}m${s.toString().padStart(2, "0")}s`;
}

function FilterBlock({
  title,
  items,
  selected,
  onToggle,
  emptyMessage = "加载中…",
}: {
  title: string;
  items: Array<{ id: number; name: string }>;
  selected: Set<number>;
  onToggle: (id: number) => void;
  emptyMessage?: string;
}) {
  return (
    <div>
      <Label className="mb-2 text-xs">
        {title}
        {selected.size > 0 && (
          <span className="ml-1 text-muted-foreground">（已选 {selected.size}）</span>
        )}
      </Label>
      <div className="flex flex-wrap gap-1.5">
        {items.length === 0 && (
          <span className="text-xs text-muted-foreground">{emptyMessage}</span>
        )}
        {items.map((it) => (
          <button
            key={it.id}
            type="button"
            onClick={() => onToggle(it.id)}
            className={cn(
              "rounded-full border px-2.5 py-0.5 text-[11px] transition-colors",
              selected.has(it.id)
                ? "border-primary bg-primary/10 text-primary"
                : "border-border text-muted-foreground hover:border-foreground/40",
            )}
          >
            {it.name}
          </button>
        ))}
      </div>
      {selected.size > 0 && (
        <button
          type="button"
          onClick={() => {
            for (const id of selected) onToggle(id);
          }}
          className="mt-1.5 text-[10px] text-muted-foreground hover:underline"
        >
          清空选择
        </button>
      )}
    </div>
  );
}

// ─── Step 2: Preview + commit ────────────────────────────────

function Step2Preview({
  batch,
  committing,
  onCommit,
  onCancel,
}: {
  batch: BulkImageBatch;
  committing: boolean;
  onCommit: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border bg-card p-5">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold">预览：{batch.zip_filename}</h3>
            <div className="mt-1 flex gap-3 text-xs text-muted-foreground">
              <span>共 {batch.total_files} 个文件</span>
              <span className="text-green-600">✓ {batch.matched_count} 已匹配</span>
              <span className="text-amber-600">? {batch.unmatched_count} 未匹配</span>
              <span className="text-red-600">✗ {batch.error_count} 错误</span>
            </div>
            {batch.error_message && (
              <div className="mt-2 text-[11px] text-amber-700">
                {batch.error_message}
              </div>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onCancel}>
              取消
            </Button>
            <Button
              size="sm"
              onClick={onCommit}
              disabled={committing || batch.matched_count === 0}
            >
              {committing && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              确认提交 {batch.matched_count} 张图片
            </Button>
          </div>
        </div>

        <Tabs defaultValue="matched" className="w-full">
          <TabsList className="w-fit">
            <TabsTrigger value="matched">
              <CheckCircle2 className="mr-1 h-3 w-3 text-green-600" />
              已匹配 ({batch.matched_count})
            </TabsTrigger>
            <TabsTrigger value="unmatched">
              <AlertCircle className="mr-1 h-3 w-3 text-amber-600" />
              未匹配 ({batch.unmatched_count})
            </TabsTrigger>
            <TabsTrigger value="error">
              <XCircle className="mr-1 h-3 w-3 text-red-600" />
              错误 ({batch.error_count})
            </TabsTrigger>
          </TabsList>

          <TabsContent value="matched" className="mt-3">
            <RowTable rows={batch.rows.filter((r) => r.status === "matched")} />
          </TabsContent>
          <TabsContent value="unmatched" className="mt-3">
            <RowTable
              rows={batch.rows.filter((r) => r.status === "unmatched")}
              showError
            />
          </TabsContent>
          <TabsContent value="error" className="mt-3">
            <RowTable
              rows={batch.rows.filter((r) => r.status === "error")}
              showError
            />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}

function RowTable({
  rows,
  showError = false,
}: {
  rows: BulkImageBatch["rows"];
  showError?: boolean;
}) {
  if (rows.length === 0) {
    return (
      <p className="py-8 text-center text-xs text-muted-foreground">无数据</p>
    );
  }
  return (
    <div className="max-h-[400px] overflow-y-auto rounded border border-border/60">
      <table className="w-full text-xs">
        <thead className="sticky top-0 border-b bg-muted/30">
          <tr>
            <th className="px-3 py-2 text-left font-normal">国家</th>
            <th className="px-3 py-2 text-left font-normal">港口</th>
            <th className="px-3 py-2 text-left font-normal">产品代码</th>
            <th className="px-3 py-2 text-left font-normal">文件名</th>
            <th className="px-3 py-2 text-right font-normal">大小</th>
            {showError && (
              <th className="px-3 py-2 text-left font-normal">原因</th>
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-border/30">
              <td className="px-3 py-1.5">{r.country_name ?? "—"}</td>
              <td className="px-3 py-1.5">{r.port_name ?? "—"}</td>
              <td className="px-3 py-1.5 font-mono">{r.product_code ?? "—"}</td>
              <td className="px-3 py-1.5 truncate max-w-[200px]">
                {r.image_filename}
              </td>
              <td className="px-3 py-1.5 text-right text-muted-foreground">
                {(r.file_size_bytes / 1024).toFixed(0)} KB
              </td>
              {showError && (
                <td className="px-3 py-1.5 text-[11px] text-muted-foreground">
                  {r.error_message ?? "—"}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Step 3: Progress + done report ──────────────────────────

function Step3Progress({
  batch,
  processingElapsedSec,
  onReset,
  onForceCancel,
}: {
  batch: BulkImageBatch;
  processingElapsedSec: number;
  onReset: () => void;
  onForceCancel: () => void;
}) {
  const isDone = batch.status === "completed";
  const isError = batch.status === "error";
  const pct =
    batch.matched_count > 0
      ? Math.round((batch.ingested_count / batch.matched_count) * 100)
      : 0;
  const failedCount = batch.rows.filter(
    (r) => r.status === "committed_failed",
  ).length;
  // If the batch has been in `processing` for > 15 minutes we assume
  // the background worker died. Backend GC will catch it within ~1h,
  // but the UI exposes a force-cancel button so the user isn't stuck.
  const isStalled = !isDone && !isError && processingElapsedSec > 15 * 60;

  return (
    <div className="rounded-lg border border-border bg-card p-5">
      <h3 className="mb-1 text-sm font-semibold">
        {isDone ? "完成" : isError ? "出错" : isStalled ? "任务卡住" : "正在入库…"}
      </h3>
      {!isDone && !isError && !isStalled && (
        <>
          <p className="mb-4 text-xs text-muted-foreground">
            正在把 {batch.matched_count} 张图片入库 — 完成 {batch.ingested_count}
            （{pct}%）。这个过程在后台跑，关页面也不会中断；做完会自动刷新。
          </p>
          <div className="mb-4 h-2 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full bg-primary transition-all"
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            正在处理… 已用时 {_fmtElapsed(processingElapsedSec)}
          </div>
        </>
      )}

      {isStalled && (
        <div className="space-y-3">
          <div className="rounded border border-amber-500/40 bg-amber-50 p-3 text-xs text-amber-900">
            任务已运行 {_fmtElapsed(processingElapsedSec)}，仍未完成 —
            后台工作进程可能已中断（Cloud Run 冷启动 / CPU 限流 / 崩溃）。
            <br />
            已成功入库 <strong>{batch.ingested_count}</strong> 张图片仍然保留。
            你可以强制取消这个批次然后重新上传剩余的图片。系统的自动清理任务
            也会在 15 分钟到 1 小时内自动处理。
          </div>
          <div className="flex gap-2">
            <Button size="sm" variant="destructive" onClick={onForceCancel}>
              强制取消
            </Button>
            <Button size="sm" variant="outline" onClick={onReset}>
              返回第一步
            </Button>
          </div>
        </div>
      )}

      {isDone && (
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm text-green-700">
            <CheckCircle2 className="h-4 w-4" />
            成功入库 {batch.ingested_count} 张图片
          </div>
          {failedCount > 0 && (
            <div className="rounded border border-amber-500/40 bg-amber-50 p-3 text-xs text-amber-900">
              其中 {failedCount} 张入库失败。可以在
              <Badge variant="outline" className="mx-1">
                /dashboard/data
              </Badge>
              产品页面手动重传，或调整模板后再试一次。
            </div>
          )}
          <Button size="sm" onClick={onReset}>
            上传另一批
          </Button>
        </div>
      )}

      {isError && (
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm text-red-700">
            <XCircle className="h-4 w-4" />
            处理失败
          </div>
          {batch.error_message && (
            <div className="rounded border border-red-500/40 bg-red-50 p-3 text-xs text-red-900">
              {batch.error_message}
            </div>
          )}
          <Button size="sm" onClick={onReset}>
            重新开始
          </Button>
        </div>
      )}
    </div>
  );
}
