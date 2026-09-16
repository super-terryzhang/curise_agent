"use client";

/**
 * Gallery dialog for viewing + managing a product's images
 * (R5 2026-06-22).
 *
 * Mounted by ProductsTab when the user clicks the row's image cell.
 * Self-contained — opens its own data fetch on mount, owns its
 * upload + delete state.
 *
 * Phase 1 scope (MVP):
 *   - 4-column grid of medium thumbnails (400px from backend)
 *   - Click thumbnail → full-screen lightbox
 *   - Drag-drop OR file picker to upload
 *   - Delete-on-hover icon
 *
 * Phase 2 (deferred):
 *   - Drag-to-reorder (dnd-kit)
 *   - Inline alt_text editing
 *   - HEIC client-side conversion
 *   - Bulk delete
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Loader2, Upload, X, ZoomIn } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  deleteProductImage,
  listProductImages,
  uploadProductImage,
  type ProductImage,
} from "@/lib/data-api";

interface Props {
  productId: number;
  productName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called after any add/delete so the parent (ProductsTab) can
   *  re-fetch the product list and update the thumbnail cell. */
  onImagesChanged?: () => void;
  /** When true, the dialog reads images on mount but disables all
   *  upload + delete affordances. Used for tests / read-only roles. */
  readOnly?: boolean;
}

export function ProductImagesGallery({
  productId,
  productName,
  open,
  onOpenChange,
  onImagesChanged,
  readOnly = false,
}: Props) {
  const [images, setImages] = useState<ProductImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listProductImages(productId);
      setImages(data);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "加载图片失败");
    } finally {
      setLoading(false);
    }
  }, [productId]);

  useEffect(() => {
    if (open) {
      refresh();
    }
  }, [open, refresh]);

  // Upload one file. We do them sequentially rather than in parallel:
  // the server-side display_order math relies on a stable count of
  // existing images at moment-of-insert, and parallel uploads would
  // race on that count. Sequential keeps semantics clean.
  const uploadFiles = useCallback(
    async (files: FileList | File[]) => {
      const list = Array.from(files);
      if (list.length === 0) return;
      setUploading(true);
      let successes = 0;
      const failures: string[] = [];
      for (const f of list) {
        try {
          await uploadProductImage(productId, f);
          successes++;
        } catch (err) {
          failures.push(
            `${f.name}: ${err instanceof Error ? err.message : "失败"}`,
          );
        }
      }
      setUploading(false);
      if (successes > 0) {
        toast.success(`已上传 ${successes} 张图片`);
      }
      if (failures.length > 0) {
        toast.error(failures.join("；"));
      }
      await refresh();
      onImagesChanged?.();
    },
    [productId, refresh, onImagesChanged],
  );

  const handleDelete = useCallback(
    async (img: ProductImage) => {
      try {
        await deleteProductImage(productId, img.id);
        toast.success("已删除图片");
        await refresh();
        onImagesChanged?.();
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "删除失败");
      }
    },
    [productId, refresh, onImagesChanged],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="truncate">{productName} · 图片</span>
            <span className="text-xs text-muted-foreground">
              ({images.length})
            </span>
          </DialogTitle>
        </DialogHeader>

        {/* Drop zone + file picker — always visible at top so users know
            where new uploads go even when the grid below is full. */}
        {!readOnly && (
          <div
            className={cn(
              "rounded border-2 border-dashed border-border/60 px-4 py-6 text-center transition-colors",
              dragOver && "border-primary bg-primary/5",
              uploading && "opacity-60 pointer-events-none",
            )}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              if (e.dataTransfer.files.length > 0) {
                uploadFiles(e.dataTransfer.files);
              }
            }}
          >
            <Upload className="mx-auto h-6 w-6 text-muted-foreground" />
            <p className="mt-2 text-xs text-muted-foreground">
              拖放图片到此处，或
              <button
                type="button"
                onClick={() => inputRef.current?.click()}
                className="ml-1 text-primary hover:underline"
                disabled={uploading}
              >
                点击选择文件
              </button>
            </p>
            <p className="mt-1 text-[10px] text-muted-foreground/60">
              JPG / PNG / WebP，单张最大 5 MB
            </p>
            <input
              ref={inputRef}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              multiple
              className="hidden"
              onChange={(e) => {
                if (e.target.files) uploadFiles(e.target.files);
                e.target.value = "";  // allow re-selecting the same file
              }}
            />
            {uploading && (
              <p className="mt-2 inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" />
                上传中…
              </p>
            )}
          </div>
        )}

        {/* Grid */}
        {loading ? (
          <div className="flex h-40 items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : images.length === 0 ? (
          <p className="py-12 text-center text-sm text-muted-foreground">
            还没有图片，{readOnly ? "等待用户上传" : "从上方拖放上传"}
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
            {images.map((img, idx) => (
              <div
                key={img.id}
                className={cn(
                  "group relative aspect-square overflow-hidden rounded border border-border/60 bg-muted/20",
                  img.display_order === 0 && "ring-2 ring-primary/40",
                )}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={img.medium_url}
                  alt={img.alt_text ?? img.filename}
                  className="h-full w-full object-cover transition-transform group-hover:scale-105"
                  loading="lazy"
                />
                {img.display_order === 0 && (
                  <span className="absolute left-1 top-1 rounded bg-primary px-1.5 py-0.5 text-[9px] font-medium text-primary-foreground">
                    主图
                  </span>
                )}
                {/* Hover-revealed actions. We use opacity rather than
                    display:none so the layout shift is zero. */}
                <div className="absolute inset-0 flex items-end justify-end gap-1 p-1.5 opacity-0 transition-opacity group-hover:opacity-100">
                  <button
                    type="button"
                    onClick={() => setLightboxIndex(idx)}
                    className="rounded bg-black/60 p-1 text-white hover:bg-black/80"
                    title="查看大图"
                  >
                    <ZoomIn className="h-3 w-3" />
                  </button>
                  {!readOnly && (
                    <button
                      type="button"
                      onClick={() => {
                        if (
                          window.confirm(
                            `确定删除「${img.filename}」吗？`,
                          )
                        ) {
                          handleDelete(img);
                        }
                      }}
                      className="rounded bg-red-600 p-1 text-white hover:bg-red-700"
                      title="删除"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Lightbox — a second Dialog nested over the gallery. Using a
            separate dialog instance keeps the gallery state intact when
            the user closes the lightbox. */}
        {lightboxIndex !== null && images[lightboxIndex] && (
          <Lightbox
            images={images}
            startIndex={lightboxIndex}
            onClose={() => setLightboxIndex(null)}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}


// ─── Lightbox ─────────────────────────────────────────────────

function Lightbox({
  images,
  startIndex,
  onClose,
}: {
  images: ProductImage[];
  startIndex: number;
  onClose: () => void;
}) {
  const [index, setIndex] = useState(startIndex);
  const current = images[index];

  // Keyboard nav: ← → cycles, Esc closes. Mounted on the dialog
  // content so it doesn't fight with the outer dialog's keymap.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft")
        setIndex((i) => (i - 1 + images.length) % images.length);
      if (e.key === "ArrowRight") setIndex((i) => (i + 1) % images.length);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [images.length, onClose]);

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-[90vw] max-h-[90vh] p-0">
        {/* Hidden title — Radix Dialog requires a DialogTitle for screen
            readers; without it React logs a console warning even though
            the visible UI is the filename caption below. */}
        <DialogTitle className="sr-only">
          {current.filename} — 第 {index + 1} / {images.length} 张
        </DialogTitle>
        <div className="relative flex flex-col items-center justify-center bg-black">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={current.full_url}
            alt={current.alt_text ?? current.filename}
            className="max-h-[80vh] max-w-full object-contain"
          />
          {images.length > 1 && (
            <>
              <Button
                variant="ghost"
                size="icon"
                onClick={() =>
                  setIndex((i) => (i - 1 + images.length) % images.length)
                }
                className="absolute left-2 top-1/2 -translate-y-1/2 text-white hover:bg-white/10"
                aria-label="上一张"
              >
                ‹
              </Button>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setIndex((i) => (i + 1) % images.length)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-white hover:bg-white/10"
                aria-label="下一张"
              >
                ›
              </Button>
            </>
          )}
          <div className="w-full bg-black/80 px-4 py-2 text-xs text-white">
            <p className="truncate">{current.filename}</p>
            <p className="text-white/60">
              第 {index + 1} / {images.length} 张 · {current.file_type}
            </p>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
