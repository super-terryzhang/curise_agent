"use client";

import { useCallback, useEffect, useState } from "react";
import { Download, Loader2, RotateCcw } from "lucide-react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  getProductBatchOriginalUrl,
  listProductBatches,
  rollbackProductBatch,
  type WorkflowBatch,
} from "@/lib/product-upload-api";

const STATUS_LABELS: Record<string, string> = {
  ready: "待检查",
  resolved: "已检查",
  completed: "已提交",
  cancelled: "已取消",
  rolled_back: "已回滚",
  failed: "失败",
};

export function UploadHistory() {
  const [items, setItems] = useState<WorkflowBatch[]>([]);
  const [loading, setLoading] = useState(true);
  const [rollbackTarget, setRollbackTarget] = useState<WorkflowBatch | null>(null);
  const [rollingBack, setRollingBack] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems((await listProductBatches()).items);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "读取上传记录失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const download = async (batch: WorkflowBatch) => {
    try {
      const url = await getProductBatchOriginalUrl(batch.id);
      window.open(url, "_blank", "noopener,noreferrer");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "原文件暂时无法下载");
    }
  };

  const rollback = async () => {
    if (!rollbackTarget) return;
    setRollingBack(true);
    try {
      await rollbackProductBatch(rollbackTarget.id);
      toast.success("批次已安全回滚");
      setRollbackTarget(null);
      await load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "回滚失败");
    } finally {
      setRollingBack(false);
    }
  };

  return (
    <section className="mt-6">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold">最近上传</h2>
        <span className="text-[11px] text-muted-foreground">仅显示你自己的批次</span>
      </div>
      <div className="overflow-hidden rounded-md border bg-background">
        {loading ? (
          <div className="flex items-center justify-center gap-2 p-6 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />读取中</div>
        ) : items.length === 0 ? (
          <div className="p-6 text-center text-sm text-muted-foreground">还没有工作台上传记录</div>
        ) : (
          <div className="divide-y">
            {items.map((batch) => (
              <div key={batch.id} className="grid gap-3 px-4 py-3 md:grid-cols-[1fr_120px_190px_auto] md:items-center">
                <div className="min-w-0"><div className="truncate text-sm font-medium">{batch.filename}</div><div className="mt-1 text-[11px] text-muted-foreground">#{batch.id} · {batch.created_at ? new Date(batch.created_at).toLocaleString("zh-CN") : "—"}{batch.file_sha256 ? ` · ${batch.file_sha256.slice(0, 10)}…` : ""}</div></div>
                <Badge variant="outline">{STATUS_LABELS[batch.status] || batch.status}</Badge>
                <div className="text-xs text-muted-foreground">新增 {batch.summary.create} · 更新 {batch.summary.update} · 错误 {batch.summary.error}</div>
                <div className="flex justify-end gap-1"><Button variant="ghost" size="sm" disabled={!batch.original_available} onClick={() => void download(batch)}><Download className="mr-1 h-3.5 w-3.5" />原件</Button>{batch.status === "completed" && <Button variant="ghost" size="sm" className="text-destructive" onClick={() => setRollbackTarget(batch)}><RotateCcw className="mr-1 h-3.5 w-3.5" />回滚</Button>}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      <AlertDialog open={Boolean(rollbackTarget)} onOpenChange={(open) => !open && setRollbackTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>回滚这个上传批次？</AlertDialogTitle><AlertDialogDescription>系统会按变更日志恢复数据；如果相关产品随后已被他人修改，冲突项不会被覆盖。</AlertDialogDescription></AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel disabled={rollingBack}>取消</AlertDialogCancel><AlertDialogAction variant="destructive" disabled={rollingBack} onClick={(event) => { event.preventDefault(); void rollback(); }}>{rollingBack ? "回滚中…" : "确认回滚"}</AlertDialogAction></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
