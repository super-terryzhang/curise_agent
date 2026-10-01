import { AlertCircle, ChevronLeft, ChevronRight, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type {
  CategoryItem,
  CountryItem,
  PortItem,
  ProductItem,
  SupplierItem,
} from "@/lib/data-api";

export interface ProductSelectionFilters {
  search: string;
  category: string;
  supplier: string;
  country: string;
  port: string;
  status: "all" | "effective" | "invalid";
}

interface ProductSelectionStepProps {
  products: ProductItem[];
  total: number;
  page: number;
  pageSize: number;
  selectedProducts: Record<number, ProductItem>;
  filters: ProductSelectionFilters;
  categories: CategoryItem[];
  suppliers: SupplierItem[];
  countries: CountryItem[];
  ports: PortItem[];
  loading: boolean;
  error: string | null;
  onFiltersChange: (filters: ProductSelectionFilters) => void;
  onSelectProduct: (product: ProductItem, selected: boolean) => void;
  onSelectVisible: (products: ProductItem[], selected: boolean) => void;
  onClearSelection: () => void;
  onPageChange: (page: number) => void;
  onRetry: () => void;
  onNext: () => void;
  onSelectAllFiltered?: () => void;
  selectingAll?: boolean;
}

const filterClassName =
  "h-9 rounded-md border border-input bg-background px-3 text-sm";

export function ProductSelectionStep({
  products,
  total,
  page,
  pageSize,
  selectedProducts,
  filters,
  categories,
  suppliers,
  countries,
  ports,
  loading,
  error,
  onFiltersChange,
  onSelectProduct,
  onSelectVisible,
  onClearSelection,
  onPageChange,
  onRetry,
  onNext,
  onSelectAllFiltered,
  selectingAll = false,
}: ProductSelectionStepProps) {
  const selectedCount = Object.keys(selectedProducts).length;
  const visibleSelected =
    products.length > 0 &&
    products.every((product) => selectedProducts[product.id]);
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const updateFilter = <K extends keyof ProductSelectionFilters>(
    key: K,
    value: ProductSelectionFilters[K],
  ) => {
    onFiltersChange({ ...filters, [key]: value });
  };

  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-base font-semibold">选择需要更新的产品</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          按现有主数据筛选产品；翻页或调整筛选不会清除已经选择的产品。
        </p>
      </div>

      <div className="grid gap-2 rounded-md border bg-muted/20 p-3 lg:grid-cols-[minmax(220px,1.6fr)_repeat(5,minmax(120px,1fr))]">
        <label className="relative">
          <Search className="absolute left-3 top-2.5 size-4 text-muted-foreground" />
          <Input
            aria-label="搜索产品"
            className="pl-9"
            placeholder="搜索产品名或代码"
            value={filters.search}
            onChange={(event) => updateFilter("search", event.target.value)}
          />
        </label>
        <select
          aria-label="类别"
          className={filterClassName}
          value={filters.category}
          onChange={(event) => updateFilter("category", event.target.value)}
        >
          <option value="all">全部类别</option>
          {categories.map((item) => (
            <option key={item.id} value={String(item.id)}>
              {item.name}
            </option>
          ))}
        </select>
        <select
          aria-label="供应商"
          className={filterClassName}
          value={filters.supplier}
          onChange={(event) => updateFilter("supplier", event.target.value)}
        >
          <option value="all">全部供应商</option>
          {suppliers.map((item) => (
            <option key={item.id} value={String(item.id)}>
              {item.name}
            </option>
          ))}
        </select>
        <select
          aria-label="国家"
          className={filterClassName}
          value={filters.country}
          onChange={(event) => updateFilter("country", event.target.value)}
        >
          <option value="all">全部国家</option>
          {countries.map((item) => (
            <option key={item.id} value={String(item.id)}>
              {item.name}
            </option>
          ))}
        </select>
        <select
          aria-label="港口"
          className={filterClassName}
          value={filters.port}
          onChange={(event) => updateFilter("port", event.target.value)}
        >
          <option value="all">全部港口</option>
          {ports.map((item) => (
            <option key={item.id} value={String(item.id)}>
              {item.name}
            </option>
          ))}
        </select>
        <select
          aria-label="状态"
          className={filterClassName}
          value={filters.status}
          onChange={(event) =>
            updateFilter(
              "status",
              event.target.value as ProductSelectionFilters["status"],
            )
          }
        >
          <option value="all">全部状态</option>
          <option value="effective">有效</option>
          <option value="invalid">无效</option>
        </select>
      </div>

      <div className="overflow-hidden rounded-md border">
        <div className="flex min-h-11 items-center justify-between border-b bg-muted/30 px-3">
          <label className="flex items-center gap-2 text-sm font-medium">
            <input
              type="checkbox"
              checked={visibleSelected}
              disabled={products.length === 0}
              onChange={(event) =>
                onSelectVisible(products, event.target.checked)
              }
            />
            选择当前页
          </label>
          <div className="flex items-center gap-3">
            <span className="text-xs text-muted-foreground">
              共 {total} 个产品
            </span>
            {onSelectAllFiltered && (
              <Button
                variant="ghost"
                size="sm"
                disabled={loading || selectingAll || total === 0}
                onClick={onSelectAllFiltered}
              >
                {selectingAll
                  ? "正在读取全部产品…"
                  : `选择筛选结果全部 ${total} 个`}
              </Button>
            )}
          </div>
        </div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-12">选择</TableHead>
              <TableHead>产品代码</TableHead>
              <TableHead>产品名称</TableHead>
              <TableHead>供应商</TableHead>
              <TableHead>国家 / 港口</TableHead>
              <TableHead>单位</TableHead>
              <TableHead>状态</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {products.map((product) => (
              <TableRow
                key={product.id}
                data-state={
                  selectedProducts[product.id] ? "selected" : undefined
                }
              >
                <TableCell>
                  <input
                    aria-label={`选择 ${product.code || product.product_name_en || product.id}`}
                    type="checkbox"
                    checked={Boolean(selectedProducts[product.id])}
                    onChange={(event) =>
                      onSelectProduct(product, event.target.checked)
                    }
                  />
                </TableCell>
                <TableCell className="font-mono text-xs">
                  {product.code || "—"}
                </TableCell>
                <TableCell>
                  <div className="max-w-[300px] whitespace-normal font-medium">
                    {product.product_name_en || "—"}
                  </div>
                  {product.product_name_jp && (
                    <div className="text-xs text-muted-foreground">
                      {product.product_name_jp}
                    </div>
                  )}
                </TableCell>
                <TableCell>{product.supplier_name || "—"}</TableCell>
                <TableCell>
                  <div>{product.country_name || "—"}</div>
                  <div className="text-xs text-muted-foreground">
                    {product.port_name || "—"}
                  </div>
                </TableCell>
                <TableCell>{product.unit || "—"}</TableCell>
                <TableCell>
                  {product.is_effective === false || product.status === false
                    ? "无效"
                    : "有效"}
                </TableCell>
              </TableRow>
            ))}
            {!loading && !error && products.length === 0 && (
              <TableRow>
                <TableCell
                  colSpan={7}
                  className="h-28 text-center text-muted-foreground"
                >
                  没有符合条件的产品
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
        {loading && (
          <div className="border-t px-4 py-8 text-center text-sm text-muted-foreground">
            正在读取产品…
          </div>
        )}
        {error && (
          <div className="flex items-center justify-center gap-3 border-t bg-destructive/5 px-4 py-6 text-sm text-destructive">
            <AlertCircle className="size-4" />
            {error}
            <Button size="sm" variant="outline" onClick={onRetry}>
              重新加载
            </Button>
          </div>
        )}
        <div className="flex items-center justify-end gap-2 border-t px-3 py-2">
          <Button
            aria-label="上一页"
            size="icon-sm"
            variant="outline"
            disabled={page <= 0 || loading}
            onClick={() => onPageChange(page - 1)}
          >
            <ChevronLeft />
          </Button>
          <span className="min-w-20 text-center text-xs text-muted-foreground">
            {page + 1} / {pageCount}
          </span>
          <Button
            aria-label="下一页"
            size="icon-sm"
            variant="outline"
            disabled={page + 1 >= pageCount || loading}
            onClick={() => onPageChange(page + 1)}
          >
            <ChevronRight />
          </Button>
        </div>
      </div>

      <div className="flex items-center justify-between border-t pt-4">
        <div className="flex items-center gap-3 text-sm">
          <span>已选择 {selectedCount} 个产品</span>
          {selectedCount > 0 && (
            <Button size="sm" variant="ghost" onClick={onClearSelection}>
              清空选择
            </Button>
          )}
        </div>
        <Button
          disabled={selectedCount === 0 || selectingAll || loading}
          onClick={onNext}
        >
          下一步
        </Button>
      </div>
    </section>
  );
}
