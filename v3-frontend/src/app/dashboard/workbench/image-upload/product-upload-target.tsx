"use client";

import { Fragment, useRef } from "react";
import { Check, ImageIcon, ImagePlus, Loader2, RefreshCw, UploadCloud } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { BulkImageStagingRow } from "@/lib/bulk-images-api";
import type { ImageUploadProductOption, ProductImage } from "@/lib/data-api";
import { cn } from "@/lib/utils";

const ACCEPT = "image/jpeg,image/png,image/webp";

type ProductImageSelectionTableProps = {
  products: ImageUploadProductOption[];
  selectedProductId: number | null;
  selectedImages: ProductImage[];
  selectedImagesLoading: boolean;
  selectedImagesError: string | null;
  stagedRows: BulkImageStagingRow[];
  busy: boolean;
  loading: boolean;
  onSelectProduct: (productId: number) => void;
  onRetryImages: () => void;
  onFiles: (files: FileList) => void;
};

export function ProductImageSelectionTable({
  products,
  selectedProductId,
  selectedImages,
  selectedImagesLoading,
  selectedImagesError,
  stagedRows,
  busy,
  loading,
  onSelectProduct,
  onRetryImages,
  onFiles,
}: ProductImageSelectionTableProps) {
  const fileRef = useRef<HTMLInputElement>(null);

  return (
    <div className="overflow-hidden rounded-md border bg-background">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[980px] border-collapse text-xs">
          <thead className="bg-slate-50 text-left text-muted-foreground dark:bg-slate-900/40">
            <tr>
              <th className="w-16 border-b border-r px-3 py-2.5 text-center font-medium">选择</th>
              <th className="w-24 border-b border-r px-3 py-2.5 font-medium">当前图片</th>
              <th className="w-36 border-b border-r px-3 py-2.5 font-medium">产品代码</th>
              <th className="border-b border-r px-3 py-2.5 font-medium">产品名称</th>
              <th className="w-40 border-b border-r px-3 py-2.5 font-medium">国家 / 港口</th>
              <th className="w-28 border-b border-r px-3 py-2.5 font-medium">现有图片</th>
              <th className="w-72 border-b px-3 py-2.5 font-medium">本次新增</th>
            </tr>
          </thead>
          <tbody>
            {products.map((product) => {
              const selected = selectedProductId === product.id;
              const additions = stagedRows.filter(
                (row) => row.product_id === product.id && row.decision !== "exclude",
              );
              const productName = product.product_name_en || product.product_name_jp || "未命名产品";
              return (
                <Fragment key={product.id}>
                  <tr className={cn("min-h-20 border-b", selected && "bg-blue-50/70 dark:bg-blue-950/20")}>
                    <td className="border-r px-3 py-3 text-center">
                      <button
                        type="button"
                        aria-label={`选择产品 ${product.code || product.id}`}
                        aria-pressed={selected}
                        onClick={() => onSelectProduct(product.id)}
                        className={cn(
                          "mx-auto flex h-5 w-5 items-center justify-center rounded border",
                          selected ? "border-primary bg-primary text-primary-foreground" : "bg-background",
                        )}
                      >
                        {selected && <Check className="h-3.5 w-3.5" />}
                      </button>
                    </td>
                    <td className="border-r px-3 py-3">
                      <button
                        type="button"
                        className="flex h-14 w-16 items-center justify-center overflow-hidden rounded border bg-muted/20"
                        onClick={() => onSelectProduct(product.id)}
                        aria-label={`查看 ${productName} 的图片`}
                      >
                        {product.thumbnail_url ? (
                          <img src={product.thumbnail_url} alt={productName} className="h-full w-full object-cover" />
                        ) : (
                          <ImageIcon className="h-5 w-5 text-muted-foreground" />
                        )}
                      </button>
                    </td>
                    <td className="border-r px-3 py-3 font-semibold tabular-nums">
                      {product.code || `#${product.id}`}
                    </td>
                    <td className="border-r px-3 py-3 text-sm">{productName}</td>
                    <td className="border-r px-3 py-3 leading-5">
                      <div>{product.country_name || "未设置国家"}</div>
                      <div className="text-muted-foreground">{product.port_name || "未设置港口"}</div>
                    </td>
                    <td className="border-r px-3 py-3 tabular-nums">现有 {product.image_count} 张</td>
                    <td className="px-3 py-3">
                      {additions.length ? (
                        <div className="flex items-center gap-2">
                          {additions.slice(0, 4).map((row) => (
                            <div key={row.id} className="h-12 w-12 shrink-0 overflow-hidden rounded border bg-muted/20">
                              {row.preview_url ? (
                                <img src={row.preview_url} alt={row.image_filename} title={row.image_filename} className="h-full w-full object-cover" />
                              ) : (
                                <ImageIcon className="m-auto mt-3.5 h-4 w-4 text-muted-foreground" />
                              )}
                            </div>
                          ))}
                          {additions.length > 4 && <span className="text-muted-foreground">+{additions.length - 4}</span>}
                        </div>
                      ) : (
                        <span className="text-muted-foreground">尚未添加</span>
                      )}
                    </td>
                  </tr>
                  {selected && (
                    <tr className="border-b bg-slate-50/60 dark:bg-slate-900/20">
                      <td colSpan={7} className="px-5 py-4">
                        <div className="grid gap-5 lg:grid-cols-[1fr_280px]">
                          <div className="min-w-0">
                            <div className="mb-2 font-semibold">现有产品图片（{product.image_count}）</div>
                            {selectedImagesLoading ? (
                              <div className="flex h-20 items-center gap-2 text-muted-foreground">
                                <Loader2 className="h-4 w-4 animate-spin" />正在读取现有图片
                              </div>
                            ) : selectedImagesError ? (
                              <div className="flex h-20 items-center justify-between gap-3 rounded border border-amber-200 bg-amber-50 px-3 text-amber-900">
                                <span>{selectedImagesError}</span>
                                <Button type="button" variant="outline" size="sm" className="h-7" onClick={onRetryImages}>
                                  <RefreshCw className="mr-1 h-3 w-3" />重新读取
                                </Button>
                              </div>
                            ) : selectedImages.length ? (
                              <div className="flex gap-2 overflow-x-auto pb-1">
                                {selectedImages.map((image, index) => (
                                  <a
                                    key={image.id}
                                    href={image.medium_url || image.full_url}
                                    target="_blank"
                                    rel="noreferrer"
                                    title={image.filename}
                                    className="relative h-20 w-20 shrink-0 overflow-hidden rounded border bg-background"
                                  >
                                    <img src={image.thumbnail_url} alt={image.filename} className="h-full w-full object-cover" />
                                    {(image.display_order === 0 || index === 0) && (
                                      <span className="absolute left-1 top-1 rounded bg-background/90 px-1.5 py-0.5 text-[9px] font-medium">主图</span>
                                    )}
                                  </a>
                                ))}
                              </div>
                            ) : (
                              <div className="flex h-20 items-center justify-center rounded border border-dashed text-muted-foreground">该产品暂无图片</div>
                            )}
                          </div>
                          <div className="flex flex-col justify-center rounded border border-dashed bg-background px-4 py-3">
                            <div className="font-semibold">添加到本次批次</div>
                            <p className="mt-1 text-[11px] text-muted-foreground">图片将明确归属于当前产品。</p>
                            <Button type="button" variant="outline" size="sm" className="mt-3 self-start" disabled={busy} onClick={() => fileRef.current?.click()}>
                              <ImagePlus className="mr-1 h-4 w-4" />为此产品选择图片
                            </Button>
                            <input
                              ref={fileRef}
                              className="hidden"
                              type="file"
                              accept={ACCEPT}
                              multiple
                              onChange={(event) => {
                                if (event.target.files) onFiles(event.target.files);
                                event.currentTarget.value = "";
                              }}
                            />
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
            {!loading && products.length === 0 && (
              <tr><td colSpan={7} className="px-4 py-12 text-center text-muted-foreground">没有找到产品</td></tr>
            )}
            {loading && (
              <tr><td colSpan={7} className="px-4 py-12 text-center text-muted-foreground"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />正在读取产品</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

type CompactImageDropzoneProps = {
  busy: boolean;
  dragging: boolean;
  progress: { done: number; total: number };
  onDraggingChange: (dragging: boolean) => void;
  onFiles: (files: FileList) => void;
};

export function CompactImageDropzone({
  busy,
  dragging,
  progress,
  onDraggingChange,
  onFiles,
}: CompactImageDropzoneProps) {
  const fileRef = useRef<HTMLInputElement>(null);

  return (
    <div
      className={cn(
        "mt-2 flex min-h-28 items-center justify-between gap-5 rounded-md border-2 border-dashed px-5 py-4 transition-colors",
        dragging && "border-primary bg-primary/5",
        busy && "pointer-events-none opacity-60",
      )}
      onDragOver={(event) => {
        event.preventDefault();
        onDraggingChange(true);
      }}
      onDragLeave={() => onDraggingChange(false)}
      onDrop={(event) => {
        event.preventDefault();
        onDraggingChange(false);
        onFiles(event.dataTransfer.files);
      }}
    >
      <div className="flex min-w-0 items-center gap-3">
        <UploadCloud className="h-7 w-7 shrink-0 text-muted-foreground" />
        <div className="min-w-0">
          <p className="text-sm font-medium">拖入图片或点击选择</p>
          <p className="mt-1 text-xs text-muted-foreground">JPG / PNG / WebP · 单张不超过 5MB · 最多 30 张/产品</p>
          {busy && progress.total > 0 && (
            <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />正在暂存 {progress.done}/{progress.total}
            </p>
          )}
        </div>
      </div>
      <Button type="button" className="shrink-0" variant="outline" disabled={busy} onClick={() => fileRef.current?.click()}>
        <ImagePlus className="mr-1 h-4 w-4" />选择图片
      </Button>
      <input
        ref={fileRef}
        className="hidden"
        type="file"
        accept={ACCEPT}
        multiple
        onChange={(event) => {
          if (event.target.files) onFiles(event.target.files);
          event.currentTarget.value = "";
        }}
      />
    </div>
  );
}
