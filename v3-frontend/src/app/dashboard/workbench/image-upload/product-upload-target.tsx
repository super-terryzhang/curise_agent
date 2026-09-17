"use client";

import { useRef } from "react";
import { ImagePlus, Loader2, RefreshCw, UploadCloud } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ProductImage, ProductItem } from "@/lib/data-api";
import { cn } from "@/lib/utils";

const ACCEPT = "image/jpeg,image/png,image/webp";

type SelectedProductImagePreviewProps = {
  product: ProductItem | null;
  images: ProductImage[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
};

export function SelectedProductImagePreview({
  product,
  images,
  loading,
  error,
  onRetry,
}: SelectedProductImagePreviewProps) {
  if (!product) {
    return (
      <div className="rounded-md border bg-muted/10 px-4 py-3">
        <div className="text-xs font-semibold">按文件名自动匹配</div>
        <p className="mt-1 text-[11px] text-muted-foreground">
          完成匹配后展示产品图库；无法唯一匹配的图片会进入程序检查。
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-md border bg-muted/10">
      <div className="flex items-start justify-between gap-3 border-b px-4 py-3">
        <div className="min-w-0">
          <div className="text-[11px] text-muted-foreground">已选择产品</div>
          <div className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xs">
            <span className="font-semibold">{product.code || `#${product.id}`}</span>
            <span className="truncate text-muted-foreground">
              {product.product_name_en || product.product_name_jp || "未命名产品"}
            </span>
          </div>
        </div>
        <div className="shrink-0 text-right text-[11px] text-muted-foreground">
          <div>{product.port_name || "未设置港口"}</div>
          <div className="mt-1">现有 {product.image_count} 张</div>
        </div>
      </div>

      <div className="px-4 py-3">
        {loading ? (
          <div className="flex h-20 items-center justify-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />正在读取现有图片
          </div>
        ) : error ? (
          <div className="flex h-20 items-center justify-between gap-3 rounded border border-amber-200 bg-amber-50 px-3 text-xs text-amber-900">
            <span>{error}</span>
            <Button type="button" variant="outline" size="sm" className="h-7 shrink-0" onClick={onRetry}>
              <RefreshCw className="mr-1 h-3 w-3" />重新读取
            </Button>
          </div>
        ) : images.length === 0 ? (
          <div className="flex h-20 items-center justify-center rounded border border-dashed text-xs text-muted-foreground">
            该产品暂无图片
          </div>
        ) : (
          <div className="flex gap-2 overflow-x-auto pb-1">
            {images.map((image, index) => (
              <a
                key={image.id}
                href={image.medium_url || image.full_url}
                target="_blank"
                rel="noreferrer"
                title={`查看 ${image.filename}`}
                className="relative h-20 w-20 shrink-0 overflow-hidden rounded border bg-background focus:outline-none focus:ring-2 focus:ring-primary/50"
              >
                <img src={image.thumbnail_url} alt={image.filename} className="h-full w-full object-cover" />
                {(image.display_order === 0 || index === 0) && (
                  <span className="absolute left-1 top-1 rounded bg-background/90 px-1.5 py-0.5 text-[9px] font-medium">
                    主图
                  </span>
                )}
              </a>
            ))}
          </div>
        )}
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
