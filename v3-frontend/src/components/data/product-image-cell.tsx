"use client";

/**
 * Cell-shaped thumbnail with optional "+N" overlay (R5 2026-06-22).
 *
 * Used in the ProductsTab table row. Stays at 32x32 to keep the row
 * compact; clicking opens the gallery dialog (mounted by the parent so
 * the cell stays cheap — no dialog is rendered when nothing is open).
 *
 * Empty state (product has zero images) renders a muted icon
 * placeholder. This is intentional — showing nothing would shift the
 * column header alignment in the table.
 */

import { ImagePlus } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  thumbnailUrl: string | null;
  imageCount: number;
  productName?: string | null;
  onClick?: (e: React.MouseEvent) => void;
}

export function ProductImageCell({
  thumbnailUrl,
  imageCount,
  productName,
  onClick,
}: Props) {
  if (!thumbnailUrl) {
    // 2026-06-22 fix: the empty state must ALSO be clickable. Otherwise
    // products with no images have no UX path to upload the first one
    // (which is the entire user-facing point of the feature). Visual
    // changes vs. the original ImageOff icon: switch to ImagePlus (a
    // "+" badge) so the affordance is obvious, give it a hover state,
    // and update the tooltip from "暂无图片" → "点击上传图片".
    return (
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onClick?.(e);
        }}
        className={cn(
          "group flex h-8 w-8 items-center justify-center rounded border border-dashed border-border/60 bg-muted/20",
          "transition-colors hover:border-primary/60 hover:bg-primary/5",
        )}
        title="点击上传图片"
      >
        <ImagePlus className="h-3.5 w-3.5 text-muted-foreground/50 transition-colors group-hover:text-primary" />
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={(e) => {
        // Prevent the row's onClick (which navigates to detail) from
        // also firing — clicking the cell thumbnail means "preview the
        // gallery", not "go to detail".
        e.stopPropagation();
        onClick?.(e);
      }}
      className={cn(
        "relative h-8 w-8 overflow-hidden rounded border border-border/40",
        "transition-transform hover:scale-110 hover:border-border/80",
      )}
      title={productName ? `${productName} (${imageCount} 张图片)` : `${imageCount} 张图片`}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={thumbnailUrl}
        alt={productName ?? "product thumbnail"}
        className="h-full w-full object-cover"
        loading="lazy"
        // Hide broken thumbnail (signed URL expired between page render and
        // image load) — fall through to the placeholder visual without
        // a noisy console error.
        onError={(e) => {
          (e.target as HTMLImageElement).style.display = "none";
        }}
      />
      {imageCount > 1 && (
        <span className="absolute -bottom-0 -right-0 rounded-tl bg-black/70 px-1 text-[8px] font-medium leading-none text-white">
          +{imageCount - 1}
        </span>
      )}
    </button>
  );
}
