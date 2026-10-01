import { AlertCircle, Download, FileSpreadsheet, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ExistingProductUpdateScope } from "@/lib/existing-product-update-workbook";

interface WorkbookStepProps {
  selectedCount: number;
  scope: ExistingProductUpdateScope;
  fileName: string | null;
  downloading: boolean;
  validating: boolean;
  error: string | null;
  onDownload: () => void;
  onFileChange: (file: File | null) => void;
  onBack: () => void;
  onValidate: () => void;
}

function scopeLabel(scope: ExistingProductUpdateScope): string {
  return [scope.basic && "基本信息", scope.purchase && "采购价区间", scope.selling && "卖价区间"]
    .filter(Boolean)
    .join("、");
}

export function WorkbookStep({
  selectedCount,
  scope,
  fileName,
  downloading,
  validating,
  error,
  onDownload,
  onFileChange,
  onBack,
  onValidate,
}: WorkbookStepProps) {
  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-base font-semibold">下载并重新上传更新文件</h2>
        <p className="mt-1 text-sm text-muted-foreground">文件包含 {selectedCount} 个产品；更新范围：{scopeLabel(scope)}。</p>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-md border p-5">
          <div className="flex items-start gap-3">
            <FileSpreadsheet className="size-5 text-amber-700" />
            <div>
              <h3 className="text-sm font-semibold">1. 下载更新文件</h3>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">打开文件后，只修改本次需要更新的单元格。不要另建文件或改变表头。</p>
            </div>
          </div>
          <div className="mt-4 rounded bg-muted/40 px-3 py-2 font-mono text-xs">已有产品更新_YYYY-MM-DD.xlsx</div>
          <Button className="mt-4 w-full" variant="outline" disabled={downloading} onClick={onDownload}>
            <Download />{downloading ? "正在生成…" : "下载更新文件"}
          </Button>
        </div>

        <div className="rounded-md border p-5">
          <div className="flex items-start gap-3">
            <Upload className="size-5 text-amber-700" />
            <div>
              <h3 className="text-sm font-semibold">2. 重新上传已修改文件</h3>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">选择刚才修改完成的同一份文件。仅支持 .xlsx。</p>
            </div>
          </div>
          <label className="mt-4 flex min-h-24 cursor-pointer flex-col items-center justify-center rounded-md border border-dashed bg-muted/20 px-4 text-center hover:bg-muted/30">
            <span className="text-sm font-medium">{fileName || "选择 Excel 文件"}</span>
            <span className="mt-1 text-xs text-muted-foreground">仅支持 .xlsx</span>
            <input
              className="sr-only"
              type="file"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={(event) => onFileChange(event.target.files?.[0] ?? null)}
            />
          </label>
        </div>
      </div>

      <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
        <strong>请保留文件结构：</strong>系统匹配列已隐藏，请勿删除、改名或修改；价格区间 ID 为空表示新增区间，保留 ID 表示更新该区间。
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertCircle className="mt-0.5 size-4 shrink-0" />{error}
        </div>
      )}

      <div className="flex justify-between border-t pt-4">
        <Button variant="outline" disabled={validating} onClick={onBack}>上一步</Button>
        <Button disabled={!fileName || validating} onClick={onValidate}>{validating ? "正在检查…" : "开始程序检查"}</Button>
      </div>
    </section>
  );
}
