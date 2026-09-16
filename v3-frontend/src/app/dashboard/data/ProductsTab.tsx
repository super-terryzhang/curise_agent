"use client";

import { useEffect, useState, useCallback } from "react";
import { type ColumnDef } from "@tanstack/react-table";
import { DataTable } from "@/components/data-table";
import { ProductPriceHistoryDialog } from "@/components/data/product-price-history";
import { ProductPricePeriodsDialog } from "@/components/data/product-price-periods";
import { ProductImageCell } from "@/components/data/product-image-cell";
import { ProductImagesGallery } from "@/components/data/product-images-gallery";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
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
import { toast } from "sonner";
import { getUser } from "@/lib/auth";
import {
  listProducts,
  listCategories,
  listSuppliers,
  listCountries,
  listPorts,
  createProduct,
  updateProduct,
  deleteProduct,
  type ProductItem,
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
  // v43+: prefer the server-computed availability so expired products
  // (effective_to < today) show as 无效 even though `status` is still
  // True. Pre-v43 backends don't return this; fall back to `status`.
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

interface ProductForm {
  product_name_en: string;
  product_name_jp: string;
  code: string;
  brand: string;
  country_id: string;
  category_id: string;
  supplier_id: string;
  port_id: string;
  price: string;
  // Contract-bound selling price (UI label: 卖价). Stored as string here
  // because the input is `<Input type="number">`; converted to float
  // before sending to the API. Empty string = NULL on save.
  contract_price: string;
  purchase_price_effective_from: string;
  purchase_price_effective_to: string;
  selling_price_effective_from: string;
  selling_price_effective_to: string;
  currency: string;
  unit: string;
  unit_size: string;
  pack_size: string;
  country_of_origin: string;
  effective_from: string;
  effective_to: string;
}

const emptyForm: ProductForm = {
  product_name_en: "", product_name_jp: "", code: "", brand: "",
  country_id: "", category_id: "", supplier_id: "", port_id: "",
  price: "", contract_price: "", purchase_price_effective_from: "",
  purchase_price_effective_to: "", selling_price_effective_from: "",
  selling_price_effective_to: "", currency: "", unit: "", unit_size: "",
  pack_size: "", country_of_origin: "", effective_from: "", effective_to: "",
};

const PAGE_SIZE = 20;

export default function ProductsTab() {
  const [products, setProducts] = useState<ProductItem[]>([]);
  const [totalProducts, setTotalProducts] = useState(0);
  const [categories, setCategories] = useState<CategoryItem[]>([]);
  const [suppliers, setSuppliers] = useState<SupplierItem[]>([]);
  const [countries, setCountries] = useState<CountryItem[]>([]);
  const [ports, setPorts] = useState<PortItem[]>([]);
  const [loading, setLoading] = useState(true);

  const [filterCategory, setFilterCategory] = useState("all");
  const [filterSupplier, setFilterSupplier] = useState("all");
  const [filterCountry, setFilterCountry] = useState("all");
  // "all" | "effective" | "invalid" — server resolves to true/false/omit.
  // Mirrors StatusBadge semantics so filter result agrees with each row's badge.
  const [filterStatus, setFilterStatus] = useState<"all" | "effective" | "invalid">("all");
  const [currentPage, setCurrentPage] = useState(0);

  const [searchText, setSearchText] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ProductItem | null>(null);
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [form, setForm] = useState<ProductForm>(emptyForm);

  // R5 (2026-06-22): track which product's gallery is open (null = none).
  // Only ONE gallery dialog ever exists at a time — clicking another
  // row's thumbnail simply swaps the productId.
  const [historyProduct, setHistoryProduct] = useState<ProductItem | null>(null);
  const [periodProduct, setPeriodProduct] = useState<ProductItem | null>(null);
  const [galleryProduct, setGalleryProduct] = useState<ProductItem | null>(null);

  const isWriter = (() => {
    const user = getUser();
    return user?.role === "superadmin" || user?.role === "admin";
  })();

  // Build filter params for server-side query
  const getFilterParams = useCallback((page: number) => {
    const params: Parameters<typeof listProducts>[0] = {
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
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
    if (filterStatus === "effective") params.is_effective = true;
    else if (filterStatus === "invalid") params.is_effective = false;
    return params;
  }, [debouncedSearch, filterCategory, filterSupplier, filterCountry, filterStatus, categories, suppliers, countries]);

  const fetchProducts = useCallback((page: number) => {
    const params = getFilterParams(page);
    listProducts(params)
      .then(({ total, items }) => {
        setProducts(items);
        setTotalProducts(total);
      })
      .catch((err) => toast.error(err.message));
  }, [getFilterParams]);

  const reload = useCallback(() => {
    fetchProducts(currentPage);
  }, [fetchProducts, currentPage]);

  // Initial load: reference data + first page of products
  useEffect(() => {
    Promise.all([listProducts({ limit: PAGE_SIZE, offset: 0 }), listCategories(), listSuppliers(), listCountries(), listPorts()])
      .then(([pRes, cat, sup, cty, pts]) => {
        setProducts(pRes.items);
        setTotalProducts(pRes.total);
        setCategories(cat);
        setSuppliers(sup);
        setCountries(cty);
        setPorts(pts);
      })
      .catch((err) => toast.error(err.message))
      .finally(() => setLoading(false));
  }, []);

  // Debounce search text → 500ms
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(searchText), 500);
    return () => clearTimeout(timer);
  }, [searchText]);

  // Re-fetch when filters or search change → reset to page 0
  useEffect(() => {
    if (!loading) {
      setCurrentPage(0);
      fetchProducts(0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterCategory, filterSupplier, filterCountry, filterStatus, debouncedSearch]);

  function openCreate() {
    setEditing(null);
    setForm(emptyForm);
    setDialogOpen(true);
  }

  function openEdit(item: ProductItem) {
    setEditing(item);
    setForm({
      product_name_en: item.product_name_en || "",
      product_name_jp: item.product_name_jp || "",
      code: item.code || "",
      brand: item.brand || "",
      country_id: item.country_id ? String(item.country_id) : "",
      category_id: item.category_id ? String(item.category_id) : "",
      supplier_id: item.supplier_id ? String(item.supplier_id) : "",
      port_id: item.port_id ? String(item.port_id) : "",
      price: item.price != null ? String(item.price) : "",
      contract_price: item.contract_price != null ? String(item.contract_price) : "",
      purchase_price_effective_from: item.purchase_price_effective_from?.slice(0, 10) || "",
      purchase_price_effective_to: item.purchase_price_effective_to?.slice(0, 10) || "",
      selling_price_effective_from: item.selling_price_effective_from?.slice(0, 10) || "",
      selling_price_effective_to: item.selling_price_effective_to?.slice(0, 10) || "",
      currency: item.currency || "",
      unit: item.unit || "",
      unit_size: item.unit_size || "",
      pack_size: item.pack_size || "",
      country_of_origin: item.country_of_origin || "",
      effective_from: item.effective_from?.slice(0, 10) || "",
      effective_to: item.effective_to?.slice(0, 10) || "",
    });
    setDialogOpen(true);
  }

  function updateForm(key: keyof ProductForm, value: string) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSave() {
    if (!form.product_name_en.trim()) {
      toast.error("英文品名不能为空");
      return;
    }
    for (const [start, end, label] of [
      [form.purchase_price_effective_from, form.purchase_price_effective_to, "采购价"],
      [form.selling_price_effective_from, form.selling_price_effective_to, "卖价"],
    ] as const) {
      if (start && end && start > end) {
        toast.error(`${label}有效开始日期不能晚于结束日期`);
        return;
      }
    }
    setSaving(true);
    try {
      // When editing, send null for cleared FK fields so backend clears them.
      // When creating, send undefined (stripped by JSON.stringify) to use defaults.
      const cleared = editing ? null : undefined;
      const payload: Record<string, unknown> = {
        product_name_en: form.product_name_en.trim(),
        product_name_jp: form.product_name_jp.trim() || cleared,
        code: form.code.trim() || cleared,
        brand: form.brand.trim() || cleared,
        country_id: form.country_id ? Number(form.country_id) : cleared,
        category_id: form.category_id ? Number(form.category_id) : cleared,
        supplier_id: form.supplier_id ? Number(form.supplier_id) : cleared,
        port_id: form.port_id ? Number(form.port_id) : cleared,
        price: form.price ? Number(form.price) : cleared,
        // contract_price: empty string → null on edit (clear the field),
        // undefined on create (DB default = NULL). 0 is a valid value
        // so we explicitly check for empty-string, not falsy.
        contract_price: form.contract_price === "" ? cleared : Number(form.contract_price),
        purchase_price_effective_from: form.purchase_price_effective_from || cleared,
        purchase_price_effective_to: form.purchase_price_effective_to || cleared,
        selling_price_effective_from: form.selling_price_effective_from || cleared,
        selling_price_effective_to: form.selling_price_effective_to || cleared,
        currency: form.currency.trim() || cleared,
        unit: form.unit.trim() || cleared,
        unit_size: form.unit_size.trim() || cleared,
        pack_size: form.pack_size.trim() || cleared,
        country_of_origin: form.country_of_origin.trim() || cleared,
        effective_from: form.effective_from || cleared,
        effective_to: form.effective_to || cleared,
      };
      if (editing) {
        await updateProduct(editing.id, { ...payload, expected_revision: editing.revision });
        toast.success("更新成功");
      } else {
        await createProduct(payload as unknown as Parameters<typeof createProduct>[0]);
        toast.success("创建成功");
      }
      setDialogOpen(false);
      reload();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "操作失败";
      if (msg.includes("已存在")) {
        // Try to find existing product and open it for editing
        try {
          const { items } = await listProducts({
            search: form.product_name_en.trim(),
            limit: 10,
          });
          const countryId = form.country_id ? Number(form.country_id) : null;
          const portId = form.port_id ? Number(form.port_id) : null;
          const existing = items.find(
            (p) =>
              p.product_name_en === form.product_name_en.trim() &&
              p.country_id === countryId &&
              p.port_id === portId
          );
          if (existing) {
            setDialogOpen(false);
            toast.info("该产品已存在，已为您打开编辑");
            openEdit(existing);
            return;
          }
        } catch {
          // fallback to just showing the error
        }
      }
      toast.error(msg);
    } finally {
      setSaving(false);
    }
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
      // R5 (2026-06-22): primary image thumbnail. Clicking opens the
      // gallery dialog without navigating away from the table. Placed
      // first so users can scan the column visually before reading codes.
      id: "thumbnail",
      header: "图片",
      size: 50,
      cell: ({ row }) => (
        <ProductImageCell
          thumbnailUrl={row.original.thumbnail_url}
          imageCount={row.original.image_count}
          productName={row.original.product_name_en}
          onClick={() => setGalleryProduct(row.original)}
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
        <span className="font-medium max-w-[200px] truncate block">
          {row.original.product_name_en || "-"}
        </span>
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
      accessorKey: "effective_from",
      header: "产品有效开始",
      size: 110,
      cell: ({ row }) => {
        const raw = row.original.effective_from;
        return (
          <span className="font-mono text-xs text-muted-foreground">
            {raw ? raw.slice(0, 10) : "-"}
          </span>
        );
      },
    },
    {
      accessorKey: "effective_to",
      header: "产品有效结束",
      size: 110,
      cell: ({ row }) => {
        // ISO datetime → YYYY-MM-DD. NULL = no expiry = blank dash.
        const raw = row.original.effective_to;
        return (
          <span className="font-mono text-xs text-muted-foreground">
            {raw ? raw.slice(0, 10) : "-"}
          </span>
        );
      },
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
        <Button variant="ghost" size="sm" className="text-xs" onClick={() => setHistoryProduct(row.original)}>
          价格历史
        </Button>
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
                  <DropdownMenuItem onClick={() => setPeriodProduct(row.original)}>
                    管理价格区间
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => openEdit(row.original)}>
                    编辑
                  </DropdownMenuItem>
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
    <div className="flex items-center gap-2 flex-1">
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

      <span className="text-xs text-muted-foreground ml-auto">
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

  return (
    <>
      <DataTable
        columns={columns}
        data={products}
        pageSize={PAGE_SIZE}
        toolbar={toolbar}
        emptyState={<EmptyState icon={Package} title="暂无产品数据" />}
        totalRows={totalProducts}
        onPageChange={(pageIndex) => {
          setCurrentPage(pageIndex);
          fetchProducts(pageIndex);
        }}
        // Default view keeps the table readable on a laptop. The R7
        // financial column `contract_price` IS visible by default —
        // that's why we added it in the first place. Lower-frequency
        // attributes (港口/品牌/原产地/单位规格/effective_from) are
        // hidden by default and toggled via the "列" button.
        defaultHiddenColumns={[
          "product_name_jp",
          "port_name",
          "brand",
          "country_of_origin",
          "unit_size",
          "effective_from",
        ]}
        visibilityStorageKey="v3.data.products.cols"
      />

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editing ? "编辑产品" : "新增产品"}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            {/* Row 1: Names */}
            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-2">
                <Label>英文品名 *</Label>
                <Input
                  value={form.product_name_en}
                  onChange={(e) => updateForm("product_name_en", e.target.value)}
                  placeholder="Product name in English"
                />
              </div>
              <div className="grid gap-2">
                <Label>日文品名</Label>
                <Input
                  value={form.product_name_jp}
                  onChange={(e) => updateForm("product_name_jp", e.target.value)}
                  placeholder="日本語の商品名"
                />
              </div>
            </div>

            {/* Row 2: Code + Brand */}
            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-2">
                <Label>商品代码</Label>
                <Input
                  value={form.code}
                  onChange={(e) => updateForm("code", e.target.value)}
                  placeholder="例如：BEEF-001"
                />
              </div>
              <div className="grid gap-2">
                <Label>品牌</Label>
                <Input
                  value={form.brand}
                  onChange={(e) => updateForm("brand", e.target.value)}
                />
              </div>
            </div>

            {/* Row 3: FK Selects */}
            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-2">
                <Label>国家</Label>
                <Select
                  value={form.country_id}
                  onValueChange={(v) => updateForm("country_id", v === "__none__" ? "" : v)}
                >
                  <SelectTrigger><SelectValue placeholder="选择国家" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">无</SelectItem>
                    {countries.map((c) => (
                      <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-2">
                <Label>类别</Label>
                <Select
                  value={form.category_id}
                  onValueChange={(v) => updateForm("category_id", v === "__none__" ? "" : v)}
                >
                  <SelectTrigger><SelectValue placeholder="选择类别" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">无</SelectItem>
                    {categories.map((c) => (
                      <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-2">
                <Label>供应商</Label>
                <Select
                  value={form.supplier_id}
                  onValueChange={(v) => updateForm("supplier_id", v === "__none__" ? "" : v)}
                >
                  <SelectTrigger><SelectValue placeholder="选择供应商" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">无</SelectItem>
                    {suppliers.map((s) => (
                      <SelectItem key={s.id} value={String(s.id)}>{s.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-2">
                <Label>港口</Label>
                <Select
                  value={form.port_id}
                  onValueChange={(v) => updateForm("port_id", v === "__none__" ? "" : v)}
                >
                  <SelectTrigger><SelectValue placeholder="选择港口" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">无</SelectItem>
                    {ports.map((p) => (
                      <SelectItem key={p.id} value={String(p.id)}>{p.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            {/* Row 4: Price (procurement) + Selling price + Currency */}
            <div className="grid grid-cols-3 gap-4">
              <div className="grid gap-2">
                <Label>采购价</Label>
                <Input
                  type="number"
                  min={0}
                  step="0.01"
                  value={form.price}
                  onChange={(e) => updateForm("price", e.target.value)}
                  placeholder="0.00"
                />
              </div>
              <div className="grid gap-2">
                <Label>
                  卖价
                  <span className="ml-1 text-[10px] font-normal text-muted-foreground">
                    (财务对比基线)
                  </span>
                </Label>
                <Input
                  type="number"
                  min={0}
                  step="0.01"
                  value={form.contract_price}
                  onChange={(e) => updateForm("contract_price", e.target.value)}
                  placeholder="0.00"
                />
              </div>
              <div className="grid gap-2">
                <Label>币种</Label>
                <Input
                  value={form.currency}
                  onChange={(e) => updateForm("currency", e.target.value)}
                  placeholder="例如：AUD"
                />
              </div>
            </div>

            {/* Row 5: Purchase price period */}
            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-2">
                <Label>采购价有效开始日期</Label>
                <Input
                  type="date"
                  value={form.purchase_price_effective_from}
                  onChange={(e) => updateForm("purchase_price_effective_from", e.target.value)}
                />
              </div>
              <div className="grid gap-2">
                <Label>采购价有效结束日期</Label>
                <Input
                  type="date"
                  value={form.purchase_price_effective_to}
                  onChange={(e) => updateForm("purchase_price_effective_to", e.target.value)}
                />
              </div>
            </div>

            {/* Row 6: Selling price period */}
            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-2">
                <Label>卖价有效开始日期</Label>
                <Input
                  type="date"
                  value={form.selling_price_effective_from}
                  onChange={(e) => updateForm("selling_price_effective_from", e.target.value)}
                />
              </div>
              <div className="grid gap-2">
                <Label>卖价有效结束日期</Label>
                <Input
                  type="date"
                  value={form.selling_price_effective_to}
                  onChange={(e) => updateForm("selling_price_effective_to", e.target.value)}
                />
              </div>
            </div>

            {/* Row 7: Unit specs */}
            <div className="grid grid-cols-3 gap-4">
              <div className="grid gap-2">
                <Label>单位</Label>
                <Input
                  value={form.unit}
                  onChange={(e) => updateForm("unit", e.target.value)}
                  placeholder="例如：KG"
                />
              </div>
              <div className="grid gap-2">
                <Label>单位规格</Label>
                <Input
                  value={form.unit_size}
                  onChange={(e) => updateForm("unit_size", e.target.value)}
                  placeholder="例如：10kg"
                />
              </div>
              <div className="grid gap-2">
                <Label>包装规格</Label>
                <Input
                  value={form.pack_size}
                  onChange={(e) => updateForm("pack_size", e.target.value)}
                  placeholder="例如：6-10ct/10kg"
                />
              </div>
            </div>

            {/* Row 8: Origin */}
            <div className="grid gap-2">
              <Label>原产地</Label>
              <Input
                value={form.country_of_origin}
                onChange={(e) => updateForm("country_of_origin", e.target.value)}
                placeholder="例如：Australia"
              />
            </div>

            {/* Row 9: Product availability dates */}
            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-2">
                <Label>产品有效开始日期</Label>
                <Input
                  type="date"
                  value={form.effective_from}
                  onChange={(e) => updateForm("effective_from", e.target.value)}
                />
              </div>
              <div className="grid gap-2">
                <Label>产品有效结束日期</Label>
                <Input
                  type="date"
                  value={form.effective_to}
                  onChange={(e) => updateForm("effective_to", e.target.value)}
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>取消</Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {editing ? "保存" : "创建"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* R5 (2026-06-22): per-product image gallery. Lazy — only mounted
          when galleryProduct is set, so cells with no clicks never pay
          for the dialog. Re-fetches the product list on changes so the
          row thumbnail stays in sync. */}
      {historyProduct && (
        <ProductPriceHistoryDialog key={historyProduct.id} product={historyProduct} canRestore={isWriter}
          onClose={() => setHistoryProduct(null)} onRestored={() => fetchProducts(currentPage)} />
      )}
      {periodProduct && (
        <ProductPricePeriodsDialog
          key={periodProduct.id}
          product={periodProduct}
          canEdit={isWriter}
          onClose={() => setPeriodProduct(null)}
          onChanged={() => fetchProducts(currentPage)}
        />
      )}
      {galleryProduct && (
        <ProductImagesGallery
          productId={galleryProduct.id}
          productName={galleryProduct.product_name_en ?? `#${galleryProduct.id}`}
          open={galleryProduct !== null}
          onOpenChange={(open) => {
            if (!open) setGalleryProduct(null);
          }}
          onImagesChanged={() => fetchProducts(currentPage)}
          readOnly={!isWriter}
        />
      )}
    </>
  );
}
