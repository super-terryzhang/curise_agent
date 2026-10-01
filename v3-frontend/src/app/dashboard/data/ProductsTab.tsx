"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type ColumnDef } from "@tanstack/react-table";
import { DataTable } from "@/components/data-table";
import { ProductImageCell } from "@/components/data/product-image-cell";
import { ProductFormDialog } from "@/components/data/product-form-dialog";
import { ProductProfitMargin } from "@/components/data/product-profit-margin";
import {
  ProductGalleryGrid,
  ProductViewToggle,
  type ProductView,
} from "@/components/data/product-gallery-grid";
import {
  createLatestRequestRunner,
  loadProductPage,
} from "@/components/data/product-gallery-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState } from "@/components/empty-state";
import { Loader2, Package, Download, Plus, MoreHorizontal, Search } from "lucide-react";
import { exportProductPrices } from "@/lib/export-products";
import { getProductPortFilterParams } from "@/lib/product-filter-query";
import { productDetailHref } from "@/lib/product-detail-route";
import { toast } from "sonner";
import { getUser } from "@/lib/auth";
import {
  listProducts,
  listCategories,
  listSuppliers,
  listCountries,
  listPorts,
  updateProduct,
  deleteProduct,
  type ProductItem,
  type ProductSort,
  type CategoryItem,
  type SupplierItem,
  type CountryItem,
  type PortItem,
} from "@/lib/data-api";

function StatusBadge({
  status,
  isEffective,
}: {
  status: boolean | null;
  // Prefer the server compatibility projection; fall back to status.
  isEffective?: boolean | null;
}) {
  const effective =
    isEffective !== undefined && isEffective !== null ? isEffective : status;
  if (effective === true || effective === null) {
    return (
      <Badge variant="secondary" className="bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300">
        有效
      </Badge>
    );
  }
  return (
    <Badge variant="secondary" className="bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300">
      无效
    </Badge>
  );
}

function PricePeriodSummary({
  product,
  type,
}: {
  product: ProductItem;
  type: "purchase" | "selling";
}) {
  const periods = (product.price_periods ?? []).filter(
    (period) => period.price_type === type && period.status,
  );
  if (periods.length === 0) {
    const start = type === "purchase"
      ? product.purchase_price_effective_from
      : product.selling_price_effective_from;
    const end = type === "purchase"
      ? product.purchase_price_effective_to
      : product.selling_price_effective_to;
    return (
      <span className="text-xs text-amber-700">
        {start || end ? `${start?.slice(0, 10) || "未填写"} 至 ${end?.slice(0, 10) || "未填写"}` : "未配置（使用旧价格）"}
      </span>
    );
  }
  return (
    <div className="space-y-0.5 font-mono text-[11px] text-muted-foreground">
      {periods.slice(0, 2).map((period) => (
        <div key={period.id}>{period.effective_from.slice(0, 10)} 至 {period.effective_to.slice(0, 10)}</div>
      ))}
      {periods.length > 2 && <div>另有 {periods.length - 2} 个区间</div>}
    </div>
  );
}


const LIST_PAGE_SIZE = 20;
const GALLERY_PAGE_SIZE = 24;

interface ProductsTabProps {
  initialSearch?: string;
}

export default function ProductsTab({ initialSearch = "" }: ProductsTabProps) {
  const router = useRouter();
  const [products, setProducts] = useState<ProductItem[]>([]);
  const [totalProducts, setTotalProducts] = useState(0);
  const [categories, setCategories] = useState<CategoryItem[]>([]);
  const [suppliers, setSuppliers] = useState<SupplierItem[]>([]);
  const [countries, setCountries] = useState<CountryItem[]>([]);
  const [ports, setPorts] = useState<PortItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [productsRefreshing, setProductsRefreshing] = useState(false);
  const [productsError, setProductsError] = useState<string | null>(null);

  const [filterCategory, setFilterCategory] = useState("all");
  const [filterSupplier, setFilterSupplier] = useState("all");
  const [filterCountry, setFilterCountry] = useState("all");
  const [filterPort, setFilterPort] = useState("all");
  // "all" | "effective" | "invalid" — server resolves to true/false/omit.
  // Mirrors StatusBadge semantics so filter result agrees with each row's badge.
  const [filterStatus, setFilterStatus] = useState<"all" | "effective" | "invalid">("all");
  const [currentPage, setCurrentPage] = useState(0);
  const [listPaginationVersion, setListPaginationVersion] = useState(0);
  const [view, setView] = useState<ProductView>("list");
  const [gallerySort, setGallerySort] = useState<ProductSort>("latest");
  const activePageSize = view === "gallery" ? GALLERY_PAGE_SIZE : LIST_PAGE_SIZE;

  const [searchText, setSearchText] = useState(initialSearch);
  const [debouncedSearch, setDebouncedSearch] = useState(initialSearch);
  const productRequestRunner = useRef(createLatestRequestRunner()).current;

  const [dialogOpen, setDialogOpen] = useState(false);
  const [exporting, setExporting] = useState(false);

  const isWriter = (() => {
    const user = getUser();
    return user?.role === "superadmin" || user?.role === "admin";
  })();

  // Build filter params for server-side query
  const getFilterParams = useCallback((
    page: number,
    pageSize = activePageSize,
    sort?: ProductSort,
  ) => {
    const params: Parameters<typeof listProducts>[0] = {
      limit: pageSize,
      offset: page * pageSize,
    };
    if (debouncedSearch) params.search = debouncedSearch;
    // Map filter name back to id for server-side filtering
    if (filterCategory !== "all") {
      const cat = categories.find((c) => c.name === filterCategory);
      if (cat) params.category_id = cat.id;
    }
    if (filterSupplier !== "all") {
      const sup = suppliers.find((s) => s.name === filterSupplier);
      if (sup) params.supplier_id = sup.id;
    }
    if (filterCountry !== "all") {
      const cty = countries.find((c) => c.name === filterCountry);
      if (cty) params.country_id = cty.id;
    }
    Object.assign(params, getProductPortFilterParams(filterPort));
    if (filterStatus === "effective") params.is_effective = true;
    else if (filterStatus === "invalid") params.is_effective = false;
    if (sort) params.sort = sort;
    return params;
  }, [activePageSize, debouncedSearch, filterCategory, filterSupplier, filterCountry, filterPort, filterStatus, categories, suppliers, countries]);

  const fetchProducts = useCallback(async (
    page: number,
    pageSize = activePageSize,
    sortOverride?: ProductSort | null,
  ) => {
    const sort = sortOverride === undefined
      ? (view === "gallery" ? gallerySort : undefined)
      : (sortOverride ?? undefined);
    setProductsRefreshing(true);
    setProductsError(null);
    try {
      const result = await productRequestRunner.run(() =>
        loadProductPage(page, pageSize, (requestedPage) =>
          listProducts(getFilterParams(requestedPage, pageSize, sort)),
        ),
      );
      if (!result) return;

      if (result.page !== page) {
        setCurrentPage(result.page);
        setListPaginationVersion((version) => version + 1);
      }
      setProducts(result.items);
      setTotalProducts(result.total);
      setProductsRefreshing(false);
    } catch (error) {
      const message = error instanceof Error ? error.message : "产品加载失败";
      setProductsError(message);
      setProductsRefreshing(false);
      toast.error(message);
    }
  }, [activePageSize, gallerySort, getFilterParams, productRequestRunner, view]);

  const reload = useCallback(() => {
    fetchProducts(currentPage);
  }, [fetchProducts, currentPage]);

  function handleViewChange(nextView: ProductView) {
    if (nextView === view) return;
    const nextPageSize = nextView === "gallery" ? GALLERY_PAGE_SIZE : LIST_PAGE_SIZE;
    setView(nextView);
    setCurrentPage(0);
    fetchProducts(
      0,
      nextPageSize,
      nextView === "gallery" ? gallerySort : null,
    );
  }

  function handleGallerySortChange(nextSort: ProductSort) {
    if (nextSort === gallerySort) return;
    setGallerySort(nextSort);
    setCurrentPage(0);
    fetchProducts(0, GALLERY_PAGE_SIZE, nextSort);
  }

  // Initial load: reference data + first page of products
  useEffect(() => {
    Promise.all([listProducts({ search: initialSearch || undefined, limit: LIST_PAGE_SIZE, offset: 0 }), listCategories(), listSuppliers(), listCountries(), listPorts()])
      .then(([pRes, cat, sup, cty, pts]) => {
        setProducts(pRes.items);
        setTotalProducts(pRes.total);
        setCategories(cat);
        setSuppliers(sup);
        setCountries(cty);
        setPorts(pts);
      })
      .catch((err) => {
        const message = err instanceof Error ? err.message : "产品加载失败";
        setProductsError(message);
        toast.error(message);
      })
      .finally(() => setLoading(false));
  }, [initialSearch]);

  // Debounce search text → 500ms
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(searchText), 500);
    return () => clearTimeout(timer);
  }, [searchText]);

  // Re-fetch when filters or search change → reset to page 0
  useEffect(() => {
    if (!loading) {
      setCurrentPage(0);
      setListPaginationVersion((version) => version + 1);
      fetchProducts(0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterCategory, filterSupplier, filterCountry, filterPort, filterStatus, debouncedSearch]);

  function openCreate() {
    setDialogOpen(true);
  }

  function openEdit(item: ProductItem) {
    router.push(productDetailHref(item.id, { tab: "basic", edit: true }));
  }


  async function handleToggleStatus(item: ProductItem) {
    try {
      await updateProduct(item.id, { status: !item.status, expected_revision: item.revision });
      toast.success(item.status ? "已停用" : "已启用");
      reload();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "操作失败");
    }
  }

  async function handleDelete(item: ProductItem) {
    if (!confirm(`确定要删除产品「${item.product_name_en}」吗？`)) return;
    try {
      await deleteProduct(item.id, item.revision);
      toast.success("删除成功");
      reload();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    }
  }

  const columns: ColumnDef<ProductItem>[] = [
    {
      // Primary image thumbnail links to the product's image panel.
      id: "thumbnail",
      header: "图片",
      size: 50,
      cell: ({ row }) => (
        <ProductImageCell
          thumbnailUrl={row.original.thumbnail_url}
          imageCount={row.original.image_count}
          productName={row.original.product_name_en}
          onClick={() => router.push(productDetailHref(row.original.id, { tab: "images" }))}
        />
      ),
    },
    {
      accessorKey: "code",
      header: "商品代码",
      size: 100,
      cell: ({ row }) => (
        <span className="font-mono text-muted-foreground">
          {row.original.code || "-"}
        </span>
      ),
    },
    {
      accessorKey: "product_name_en",
      header: "英文品名",
      cell: ({ row }) => (
        <Link href={productDetailHref(row.original.id)} className="font-medium max-w-[200px] truncate block hover:underline">
          {row.original.product_name_en || "-"}
        </Link>
      ),
    },
    {
      accessorKey: "product_name_jp",
      header: "日文品名",
      size: 160,
      cell: ({ row }) => (
        <span
          className="block max-w-[160px] truncate text-muted-foreground"
          title={row.original.product_name_jp || undefined}
        >
          {row.original.product_name_jp || "-"}
        </span>
      ),
    },
    {
      accessorKey: "category_name",
      header: "类别",
      size: 100,
      cell: ({ row }) => row.original.category_name || "-",
    },
    {
      accessorKey: "supplier_name",
      header: "供应商",
      size: 120,
      cell: ({ row }) => row.original.supplier_name || "-",
    },
    {
      accessorKey: "country_name",
      header: "国家",
      size: 80,
      cell: ({ row }) => row.original.country_name || "-",
    },
    {
      accessorKey: "port_name",
      header: "港口",
      size: 100,
      cell: ({ row }) => row.original.port_name || "-",
    },
    {
      accessorKey: "brand",
      header: "品牌",
      size: 100,
      cell: ({ row }) => row.original.brand || "-",
    },
    {
      accessorKey: "country_of_origin",
      header: "原产地",
      size: 100,
      cell: ({ row }) => row.original.country_of_origin || "-",
    },
    {
      accessorKey: "unit",
      header: "单位",
      size: 60,
      cell: ({ row }) => (
        <span className="text-center block text-xs">
          {row.original.unit || "-"}
        </span>
      ),
    },
    {
      accessorKey: "unit_size",
      header: "规格",
      size: 90,
      cell: ({ row }) => row.original.unit_size || "-",
    },
    {
      accessorKey: "pack_size",
      header: "包装",
      size: 100,
      cell: ({ row }) => row.original.pack_size || "-",
    },
    {
      accessorKey: "currency",
      header: "币种",
      size: 70,
      cell: ({ row }) => (
        <span className="font-mono text-xs">{row.original.currency || "-"}</span>
      ),
    },
    {
      accessorKey: "price",
      header: () => <span className="text-right block">采购价</span>,
      size: 100,
      cell: ({ row }) => {
        const { price, currency } = row.original;
        if (price == null) return <span className="text-right block">-</span>;
        return (
          <span className="text-right block">
            {currency ? `${currency} ` : ""}{price}
          </span>
        );
      },
    },
    {
      id: "purchase_price_period",
      header: "采购价有效期",
      size: 180,
      cell: ({ row }) => {
        return <PricePeriodSummary product={row.original} type="purchase" />;
      },
    },
    {
      id: "selling_price_period",
      header: "卖价有效期",
      size: 180,
      cell: ({ row }) => {
        return <PricePeriodSummary product={row.original} type="selling" />;
      },
    },
    {
      accessorKey: "contract_price",
      header: () => <span className="text-right block">卖价</span>,
      size: 100,
      cell: ({ row }) => {
        // R7 financial: contract_price is the price we own as the seller.
        // Display in tabular-nums for visual lineup with price column.
        const { contract_price, currency } = row.original;
        if (contract_price == null) {
          return <span className="text-right block text-muted-foreground/50">-</span>;
        }
        return (
          <span className="text-right block tabular-nums font-medium">
            {currency ? `${currency} ` : ""}{contract_price}
          </span>
        );
      },
    },
    {
      accessorKey: "profit_margin",
      header: () => <span className="text-right block">利润率</span>,
      size: 90,
      cell: ({ row }) => (
        <div className="text-right">
          <ProductProfitMargin value={row.original.profit_margin} />
        </div>
      ),
    },
    {
      accessorKey: "status",
      header: "状态",
      size: 70,
      cell: ({ row }) => (
        <StatusBadge
          status={row.original.status}
          isEffective={row.original.is_effective}
        />
      ),
    },
    {
      id: "price_history",
      header: "价格记录",
      size: 100,
      cell: ({ row }) => (
        <Button asChild variant="ghost" size="sm" className="text-xs"><Link href={productDetailHref(row.original.id, { tab: "prices" })}>价格历史</Link></Button>
      ),
    },
    ...(isWriter
      ? [
          {
            id: "actions",
            header: "操作",
            size: 60,
            cell: ({ row }: { row: { original: ProductItem } }) => (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                    <MoreHorizontal className="h-4 w-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem asChild><Link href={productDetailHref(row.original.id, { tab: "prices" })}>管理价格区间</Link></DropdownMenuItem>
                  <DropdownMenuItem asChild><Link href={productDetailHref(row.original.id, { tab: "basic", edit: true })}>编辑</Link></DropdownMenuItem>
                  <DropdownMenuItem onClick={() => handleToggleStatus(row.original)}>
                    {row.original.status ? "停用" : "启用"}
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    className="text-red-600"
                    onClick={() => handleDelete(row.original)}
                  >
                    删除
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            ),
          } as ColumnDef<ProductItem>,
        ]
      : []),
  ];

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const toolbar = (
    <div className="flex flex-1 flex-wrap items-center gap-2">
      <div className="relative max-w-xs">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
        <Input
          placeholder="搜索产品名或代码..."
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          className="pl-9 h-8 text-xs w-56"
        />
      </div>

      <Select value={filterCategory} onValueChange={setFilterCategory}>
        <SelectTrigger className="h-8 w-32 text-xs">
          <SelectValue placeholder="类别" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部类别</SelectItem>
          {categories.map((c) => (
            <SelectItem key={c.id} value={c.name}>{c.name}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select value={filterSupplier} onValueChange={setFilterSupplier}>
        <SelectTrigger className="h-8 w-32 text-xs">
          <SelectValue placeholder="供应商" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部供应商</SelectItem>
          {suppliers.map((s) => (
            <SelectItem key={s.id} value={s.name}>{s.name}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select value={filterCountry} onValueChange={setFilterCountry}>
        <SelectTrigger className="h-8 w-32 text-xs">
          <SelectValue placeholder="国家" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部国家</SelectItem>
          {countries.map((c) => (
            <SelectItem key={c.id} value={c.name}>{c.name}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select value={filterPort} onValueChange={setFilterPort}>
        <SelectTrigger className="h-8 w-32 text-xs">
          <SelectValue placeholder="港口" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部港口</SelectItem>
          {ports.map((port) => (
            <SelectItem key={port.id} value={String(port.id)}>
              {port.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={filterStatus}
        onValueChange={(v) => {
          setFilterStatus(v as "all" | "effective" | "invalid");
          setCurrentPage(0);
        }}
      >
        <SelectTrigger className="h-8 w-28 text-xs">
          <SelectValue placeholder="状态" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部状态</SelectItem>
          <SelectItem value="effective">有效</SelectItem>
          <SelectItem value="invalid">无效</SelectItem>
        </SelectContent>
      </Select>

      <div className="ml-auto">
        <ProductViewToggle view={view} onViewChange={handleViewChange} />
      </div>
      <span className="text-xs text-muted-foreground">
        共 {totalProducts} 个产品
      </span>
      <Button
        variant="outline"
        size="sm"
        className="h-8 text-xs"
        disabled={exporting || totalProducts === 0}
        title="导出当前筛选的全部产品，包含采购价和卖价，可用于批量更新"
        onClick={async () => {
          setExporting(true);
          try {
            const count = await exportProductPrices(getFilterParams(0));
            toast.success(`已导出当前筛选的全部 ${count} 个产品`);
          } catch (err) {
            toast.error(err instanceof Error ? err.message : "导出失败");
          } finally {
            setExporting(false);
          }
        }}
      >
        {exporting ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Download className="mr-1 h-3 w-3" />}
        {exporting ? "正在导出…" : "导出价格 Excel（全部筛选结果）"}
      </Button>
      {isWriter && (
        <Button size="sm" className="h-8 text-xs" onClick={openCreate}>
          <Plus className="mr-1 h-3 w-3" /> 新增产品
        </Button>
      )}
    </div>
  );

  const productEmptyState = productsRefreshing ? (
    <div className="flex flex-col items-center justify-center py-16 text-sm text-muted-foreground">
      <Loader2 className="mb-3 h-6 w-6 animate-spin" />
      正在加载产品…
    </div>
  ) : productsError ? (
    <EmptyState
      icon={Package}
      title="产品加载失败"
      description={productsError}
      action={(
        <Button variant="outline" size="sm" onClick={() => void fetchProducts(currentPage)}>
          重新加载
        </Button>
      )}
    />
  ) : (
    <EmptyState icon={Package} title="暂无产品数据" />
  );

  return (
    <>
      {view === "list" ? (
        <DataTable
          key={`products-list-${listPaginationVersion}`}
          columns={columns}
          data={productsRefreshing || productsError ? [] : products}
          pageSize={LIST_PAGE_SIZE}
          toolbar={toolbar}
          emptyState={productEmptyState}
          totalRows={totalProducts}
          onPageChange={(pageIndex) => {
            setCurrentPage(pageIndex);
            fetchProducts(pageIndex, LIST_PAGE_SIZE);
          }}
          // Default view keeps the table readable on a laptop. The R7
          // financial column `contract_price` IS visible by default —
          // that's why we added it in the first place. Lower-frequency
          // attributes (港口/品牌/原产地/单位规格) are
          // hidden by default and toggled via the "列" button.
          defaultHiddenColumns={[
            "product_name_jp",
            "port_name",
            "brand",
            "country_of_origin",
            "unit_size",
          ]}
          visibilityStorageKey="v3.data.products.cols"
        />
      ) : (
        <div className="flex h-full min-h-0 flex-col">
          <div className="flex shrink-0 items-center gap-3 px-4 py-3">
            {toolbar}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
            {productsRefreshing || productsError ? (
              productEmptyState
            ) : (
              <ProductGalleryGrid
                products={products}
                totalProducts={totalProducts}
                pageIndex={currentPage}
                pageSize={GALLERY_PAGE_SIZE}
                isWriter={isWriter}
                sort={gallerySort}
                onSortChange={handleGallerySortChange}
                onPageChange={(pageIndex) => {
                  setCurrentPage(pageIndex);
                  fetchProducts(pageIndex, GALLERY_PAGE_SIZE);
                }}
                onToggleStatus={(product) => void handleToggleStatus(product)}
                onDelete={(product) => void handleDelete(product)}
              />
            )}
          </div>
        </div>
      )}

      <ProductFormDialog
        open={dialogOpen}
        product={null}
        categories={categories}
        suppliers={suppliers}
        countries={countries}
        ports={ports}
        onOpenChange={setDialogOpen}
        onSaved={() => reload()}
        onDuplicate={openEdit}
      />
    </>
  );
}
