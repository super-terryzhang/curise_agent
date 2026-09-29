"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Upload, X, ZoomIn } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import {
  deleteProductImage,
  listProductImages,
  uploadProductImage,
  type ProductImage,
} from "@/lib/data-api";

interface PanelProps {
  productId: number;
  productName: string;
  onImagesChanged?: () => void;
  readOnly?: boolean;
}


export function ProductImageGrid({ images, readOnly, onView, onDelete }: {
  images: ProductImage[];
  readOnly: boolean;
  onView: (index: number) => void;
  onDelete: (image: ProductImage) => void;
}) {
  if (images.length === 0) {
    return <p className="py-12 text-center text-sm text-muted-foreground">
      还没有图片，{readOnly ? "等待有权限的用户上传" : "可从上方选择或拖放上传"}
    </p>;
  }

  return <div className="space-y-3">
    <p className="text-xs text-muted-foreground">共 {images.length} 张</p>
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
      {images.map((image, index) => <div key={image.id} className={cn(
        "group relative aspect-square overflow-hidden rounded border border-border/60 bg-muted/20",
        image.display_order === 0 && "ring-2 ring-primary/40",
      )}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={image.medium_url} alt={image.alt_text ?? image.filename}
          className="h-full w-full object-cover transition-transform group-hover:scale-105" loading="lazy" />
        {image.display_order === 0 && <span className="absolute left-1 top-1 rounded bg-primary px-1.5 py-0.5 text-[9px] font-medium text-primary-foreground">主图</span>}
        <div className="absolute inset-0 flex items-end justify-end gap-1 p-1.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
          <button type="button" onClick={() => onView(index)} className="rounded bg-black/60 p-1 text-white hover:bg-black/80" title="查看大图" aria-label={`查看大图：${image.filename}`}>
            <ZoomIn className="h-3 w-3" />
          </button>
          {!readOnly && <button type="button" onClick={() => onDelete(image)} className="rounded bg-red-600 p-1 text-white hover:bg-red-700" title="删除" aria-label={`删除：${image.filename}`}>
            <X className="h-3 w-3" />
          </button>}
        </div>
      </div>)}
    </div>
  </div>;
}

export function ProductImagesPanel({ productId, productName, onImagesChanged, readOnly = false }: PanelProps) {
  const [images, setImages] = useState<ProductImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    setLoading(true); setError("");
    try { setImages(await listProductImages(productId)); }
    catch (loadError) { setError(loadError instanceof Error ? loadError.message : "加载图片失败"); }
    finally { setLoading(false); }
  }, [productId]);

  useEffect(() => { void refresh(); }, [refresh]);

  const uploadFiles = useCallback(async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (list.length === 0) return;
    setUploading(true);
    let successes = 0;
    const failures: string[] = [];
    for (const file of list) {
      try { await uploadProductImage(productId, file); successes += 1; }
      catch (uploadError) { failures.push(`${file.name}: ${uploadError instanceof Error ? uploadError.message : "失败"}`); }
    }
    setUploading(false);
    if (successes > 0) toast.success(`已上传 ${successes} 张图片`);
    if (failures.length > 0) toast.error(failures.join("；"));
    await refresh();
    onImagesChanged?.();
  }, [onImagesChanged, productId, refresh]);

  const handleDelete = useCallback(async (image: ProductImage) => {
    if (!window.confirm(`确定删除「${image.filename}」吗？`)) return;
    try {
      await deleteProductImage(productId, image.id);
      toast.success("已删除图片");
      await refresh();
      onImagesChanged?.();
    } catch (deleteError) {
      toast.error(deleteError instanceof Error ? deleteError.message : "删除失败");
    }
  }, [onImagesChanged, productId, refresh]);

  return <section aria-labelledby="product-images-heading" className="space-y-4 rounded-md border bg-background p-5">
    <div className="flex items-start justify-between gap-4">
      <div><h2 id="product-images-heading" className="text-sm font-semibold">产品图片</h2>
        <p className="mt-1 text-xs text-muted-foreground">{productName}</p></div>
      <span className="text-xs text-muted-foreground">{images.length} 张</span>
    </div>

    {!readOnly && <div className={cn(
      "rounded border-2 border-dashed border-border/60 px-4 py-4 text-center transition-colors",
      dragOver && "border-primary bg-primary/5",
      uploading && "pointer-events-none opacity-60",
    )} onDragOver={event => { event.preventDefault(); setDragOver(true); }} onDragLeave={() => setDragOver(false)}
      onDrop={event => { event.preventDefault(); setDragOver(false); if (event.dataTransfer.files.length > 0) void uploadFiles(event.dataTransfer.files); }}>
      <div className="flex flex-wrap items-center justify-center gap-2 text-xs text-muted-foreground">
        <Upload className="h-5 w-5" /><span>拖放图片到此处，或</span>
        <button type="button" onClick={() => inputRef.current?.click()} className="text-primary hover:underline" disabled={uploading}>点击选择文件</button>
        <span>JPG / PNG / WebP，单张最大 5 MB</span>
      </div>
      <input ref={inputRef} type="file" accept="image/jpeg,image/png,image/webp" multiple className="hidden"
        onChange={event => { if (event.target.files) void uploadFiles(event.target.files); event.target.value = ""; }} />
      {uploading && <p className="mt-2 inline-flex items-center gap-1 text-[11px] text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" />上传中…</p>}
    </div>}

    {loading ? <div role="status" className="flex h-40 items-center justify-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-5 w-5 animate-spin" />正在加载图片…</div>
      : error ? <div role="alert" className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}，请<Button variant="link" className="h-auto px-1 text-red-700" onClick={() => void refresh()}>重新加载</Button>。</div>
      : <ProductImageGrid images={images} readOnly={readOnly} onView={setLightboxIndex} onDelete={image => void handleDelete(image)} />}

    {lightboxIndex !== null && images[lightboxIndex] && <Lightbox images={images} startIndex={lightboxIndex} onClose={() => setLightboxIndex(null)} />}
  </section>;
}

function Lightbox({ images, startIndex, onClose }: { images: ProductImage[]; startIndex: number; onClose: () => void }) {
  const [index, setIndex] = useState(startIndex);
  const current = images[index];

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "ArrowLeft") setIndex(value => (value - 1 + images.length) % images.length);
      if (event.key === "ArrowRight") setIndex(value => (value + 1) % images.length);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [images.length, onClose]);

  return <Dialog open onOpenChange={open => !open && onClose()}><DialogContent className="max-h-[90vh] max-w-[90vw] p-0">
    <DialogTitle className="sr-only">{current.filename} — 第 {index + 1} / {images.length} 张</DialogTitle>
    <div className="relative flex flex-col items-center justify-center bg-black">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={current.full_url} alt={current.alt_text ?? current.filename} className="max-h-[80vh] max-w-full object-contain" />
      {images.length > 1 && <><Button variant="ghost" size="icon" onClick={() => setIndex(value => (value - 1 + images.length) % images.length)}
        className="absolute left-2 top-1/2 -translate-y-1/2 text-white hover:bg-white/10" aria-label="上一张">‹</Button>
        <Button variant="ghost" size="icon" onClick={() => setIndex(value => (value + 1) % images.length)}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-white hover:bg-white/10" aria-label="下一张">›</Button></>}
      <div className="w-full bg-black/80 px-4 py-2 text-xs text-white"><p className="truncate">{current.filename}</p>
        <p className="text-white/60">第 {index + 1} / {images.length} 张 · {current.file_type}</p></div>
    </div>
  </DialogContent></Dialog>;
}
