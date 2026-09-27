"use client";

import {
  ChevronLeft,
  ChevronRight,
  ImagePlus,
  LayoutGrid,
  List,
  MoreHorizontal,
  Package,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { ProductItem } from "@/lib/data-api";

export type ProductView = "list" | "gallery";

export function ProductViewToggle({
  view,
  onViewChange,
}: {
  view: ProductView;
  onViewChange: (view: ProductView) => void;
}) {
  return (
    <div
      role="group"
      aria-label="产品展示方式"
      className="flex shrink-0 rounded-md border bg-muted/30 p-0.5"
    >
      <Button
        type="button"
        size="sm"
        variant={view === "list" ? "secondary" : "ghost"}
        aria-pressed={view === "list"}
        onClick={() => onViewChange("list")}
      >
        <List />
        列表视图
      </Button>
      <Button
        type="button"
        size="sm"
        variant={view === "gallery" ? "secondary" : "ghost"}
        aria-pressed={view === "gallery"}
        onClick={() => onViewChange("gallery")}
      >
        <LayoutGrid />
        图库视图
      </Button>
    </div>
  );
}

function productName(product: ProductItem): string {
  return (
    product.product_name_en ||
    product.product_name_jp ||
    product.code ||
    `产品 #${product.id}`
  );
}

function sellingPrice(product: ProductItem): string {
  if (product.contract_price == null) return "卖价未配置";
  const digits = product.currency === "JPY" ? 0 : 2;
  const amount = product.contract_price.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return `${product.currency ? `${product.currency} ` : ""}${amount}`;
}

function ProductEffectiveBadge({ product }: { product: ProductItem }) {
  const effective =
    product.is_effective !== undefined && product.is_effective !== null
      ? product.is_effective
      : product.status;
  if (effective === true || effective === null) {
    return (
      <Badge
        variant="secondary"
        className="bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300"
      >
        有效
      </Badge>
    );
  }
  return (
    <Badge
      variant="secondary"
      className="bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300"
    >
      无效
    </Badge>
  );
}

interface ProductGalleryGridProps {
  products: ProductItem[];
  totalProducts: number;
  pageIndex: number;
  pageSize: number;
  isWriter: boolean;
  onPageChange: (pageIndex: number) => void;
  onOpenImages: (product: ProductItem) => void;
  onOpenHistory: (product: ProductItem) => void;
  onManagePrices: (product: ProductItem) => void;
  onEdit: (product: ProductItem) => void;
  onToggleStatus: (product: ProductItem) => void;
  onDelete: (product: ProductItem) => void;
}

export function ProductGalleryGrid({
  products,
  totalProducts,
  pageIndex,
  pageSize,
  isWriter,
  onPageChange,
  onOpenImages,
  onOpenHistory,
  onManagePrices,
  onEdit,
  onToggleStatus,
  onDelete,
}: ProductGalleryGridProps) {
  const pageCount = Math.max(1, Math.ceil(totalProducts / pageSize));
  const currentPage = Math.min(pageCount, Math.max(1, pageIndex + 1));

  if (!products.length) {
    return (
      <div className="flex min-h-64 flex-col items-center justify-center rounded-lg border text-sm text-muted-foreground">
        <Package className="mb-3 h-8 w-8 opacity-40" />
        暂无产品数据
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <section
        aria-label="产品图库"
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6"
      >
        {products.map((product) => {
          const name = productName(product);
          return (
            <article
              key={product.id}
              className="group overflow-hidden rounded-lg border bg-background shadow-xs transition-shadow hover:shadow-sm"
            >
              <button
                type="button"
                aria-label={
                  product.thumbnail_url || !isWriter
                    ? `查看 ${name} 图片`
                    : `为 ${name} 上传图片`
                }
                className="relative flex h-40 w-full items-center justify-center overflow-hidden bg-muted/30"
                onClick={() => onOpenImages(product)}
              >
                {product.thumbnail_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={product.thumbnail_url}
                    alt={name}
                    className="h-full w-full object-contain p-3 transition-transform group-hover:scale-[1.03]"
                    loading="lazy"
                    onError={(event) => {
                      (event.currentTarget as HTMLImageElement).style.display =
                        "none";
                    }}
                  />
                ) : (
                  <span className="flex flex-col items-center gap-2 text-xs text-muted-foreground">
                    <ImagePlus className="h-8 w-8 opacity-40" />
                    {isWriter ? "上传图片" : "暂无图片"}
                  </span>
                )}
                {product.image_count > 1 ? (
                  <span className="absolute bottom-2 right-2 rounded-full bg-black/70 px-2 py-0.5 text-[10px] font-medium text-white">
                    {product.image_count} 张
                  </span>
                ) : null}
              </button>

              <div className="space-y-2 p-3">
                <div className="min-w-0">
                  <h3 className="truncate text-sm font-medium" title={name}>
                    {name}
                  </h3>
                  {product.product_name_jp ? (
                    <p
                      className="mt-0.5 truncate text-xs text-muted-foreground"
                      title={product.product_name_jp}
                    >
                      {product.product_name_jp}
                    </p>
                  ) : null}
                  <p className="mt-1 truncate text-xs text-muted-foreground">
                    {product.brand || "品牌未填写"} · {product.port_name || "港口未填写"}
                  </p>
                </div>

                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-sm font-semibold tabular-nums">
                    {sellingPrice(product)}
                  </span>
                  <ProductEffectiveBadge product={product} />
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`${name} 更多操作`}
                      >
                        <MoreHorizontal />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onSelect={() => onOpenHistory(product)}>
                        价格历史
                      </DropdownMenuItem>
                      {isWriter ? (
                        <>
                          <DropdownMenuItem
                            onSelect={() => onManagePrices(product)}
                          >
                            管理价格区间
                          </DropdownMenuItem>
                          <DropdownMenuItem onSelect={() => onEdit(product)}>
                            编辑
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onSelect={() => onToggleStatus(product)}
                          >
                            {product.status ? "停用" : "启用"}
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            className="text-red-600"
                            onSelect={() => onDelete(product)}
                          >
                            删除
                          </DropdownMenuItem>
                        </>
                      ) : null}
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              </div>
            </article>
          );
        })}
      </section>

      <div className="flex items-center justify-between border-t pt-3 text-xs text-muted-foreground">
        <span>共 {totalProducts} 个产品</span>
        <div className="flex items-center gap-1">
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="上一页"
            disabled={currentPage <= 1}
            onClick={() => onPageChange(currentPage - 2)}
          >
            <ChevronLeft />
          </Button>
          <span className="min-w-12 text-center">
            {currentPage} / {pageCount}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="下一页"
            disabled={currentPage >= pageCount}
            onClick={() => onPageChange(currentPage)}
          >
            <ChevronRight />
          </Button>
        </div>
      </div>
    </div>
  );
}
