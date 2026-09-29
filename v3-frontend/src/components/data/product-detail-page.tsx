"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Loader2, Pencil } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ProductBasicInfo } from "./product-basic-info";
import { ProductFormDialog } from "./product-form-dialog";
import { ProductImagesPanel } from "./product-images-gallery";
import { ProductPriceHistoryPanel } from "./product-price-history";
import { ProductPricePeriodsPanel } from "./product-price-periods";
import { getUser } from "@/lib/auth";
import {
  getProduct,
  listCategories,
  listCountries,
  listPorts,
  listSuppliers,
  type CategoryItem,
  type CountryItem,
  type PortItem,
  type ProductItem,
  type SupplierItem,
} from "@/lib/data-api";
import {
  productDetailHref,
  type ProductDetailTab,
} from "@/lib/product-detail-route";

export function ProductDetailHeader({ product, canEdit, onEdit }: {
  product: ProductItem;
  canEdit: boolean;
  onEdit: () => void;
}) {
  const effective = product.is_effective ?? product.status ?? true;
  return <header className="space-y-4 border-b pb-5">
    <Link href="/dashboard/data?tab=products" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
      <ArrowLeft className="h-3.5 w-3.5" />返回产品列表
    </Link>
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="truncate text-xl font-semibold">{product.product_name_en || product.product_name_jp || product.code || `产品 #${product.id}`}</h1>
          <Badge variant="secondary" className={effective ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}>{effective ? "有效" : "无效"}</Badge>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {product.code || "商品代码未填写"} · {product.product_name_jp || "日文品名未填写"}
        </p>
      </div>
      {canEdit && <Button size="sm" onClick={onEdit}><Pencil className="mr-1 h-3.5 w-3.5" />编辑产品</Button>}
    </div>
  </header>;
}

export function ProductDetailTabs({ productId, activeTab }: { productId: number; activeTab: ProductDetailTab }) {
  const tabs: Array<{ id: ProductDetailTab; label: string }> = [
    { id: "basic", label: "基本信息" },
    { id: "prices", label: "价格历史" },
    { id: "images", label: "产品图片" },
  ];
  return <nav aria-label="产品详情" className="flex gap-6 border-b">
    {tabs.map(tab => <Link key={tab.id} href={productDetailHref(productId, { tab: tab.id })}
      aria-current={activeTab === tab.id ? "page" : undefined}
      className={activeTab === tab.id ? "border-b-2 border-primary px-1 py-3 text-sm font-medium text-primary" : "px-1 py-3 text-sm text-muted-foreground hover:text-foreground"}>
      {tab.label}
    </Link>)}
  </nav>;
}

export function ProductDetailLoadState({ loading, error }: { loading: boolean; error: string }) {
  if (loading) return <div role="status" className="flex min-h-64 items-center justify-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-5 w-5 animate-spin" />正在加载产品…</div>;
  if (!error) return null;
  const notFound = error.includes("不存在") || error.includes("404");
  return <div role="alert" className="flex min-h-64 flex-col items-center justify-center rounded-md border text-center">
    <h1 className="text-base font-semibold">{notFound ? "找不到这个产品" : "产品加载失败"}</h1>
    <p className="mt-2 max-w-lg text-sm text-muted-foreground">{error}</p>
    <Button asChild variant="outline" className="mt-4"><Link href="/dashboard/data?tab=products">返回产品列表</Link></Button>
  </div>;
}

export function ProductDetailPage({ productId, activeTab, initialEdit = false }: {
  productId: number;
  activeTab: ProductDetailTab;
  initialEdit?: boolean;
}) {
  const [product, setProduct] = useState<ProductItem | null>(null);
  const [categories, setCategories] = useState<CategoryItem[]>([]);
  const [suppliers, setSuppliers] = useState<SupplierItem[]>([]);
  const [countries, setCountries] = useState<CountryItem[]>([]);
  const [ports, setPorts] = useState<PortItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const user = getUser();
  const canEdit = user?.role === "superadmin" || user?.role === "admin";
  const [editOpen, setEditOpen] = useState(initialEdit && canEdit);

  const load = useCallback(async () => {
    setLoading(true); setError("");
    if (!Number.isInteger(productId) || productId <= 0) {
      setProduct(null); setError("产品不存在"); setLoading(false); return;
    }
    try {
      const [nextProduct, nextCategories, nextSuppliers, nextCountries, nextPorts] = await Promise.all([
        getProduct(productId), listCategories(), listSuppliers(), listCountries(), listPorts(),
      ]);
      setProduct(nextProduct);
      setCategories(nextCategories);
      setSuppliers(nextSuppliers);
      setCountries(nextCountries);
      setPorts(nextPorts);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "产品加载失败");
    } finally {
      setLoading(false);
    }
  }, [productId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (initialEdit && canEdit) setEditOpen(true); }, [canEdit, initialEdit]);

  if (loading || error || !product) return <div className="p-6"><ProductDetailLoadState loading={loading} error={error} /></div>;

  return <main className="h-full overflow-y-auto p-6">
    <div className="mx-auto max-w-7xl space-y-5">
      <ProductDetailHeader product={product} canEdit={canEdit} onEdit={() => setEditOpen(true)} />
      <ProductDetailTabs productId={product.id} activeTab={activeTab} />
      {activeTab === "basic" && <ProductBasicInfo product={product} />}
      {activeTab === "prices" && <div className="space-y-5">
        <ProductPricePeriodsPanel product={product} canEdit={canEdit} onChanged={() => void load()} />
        <ProductPriceHistoryPanel product={product} canRestore={canEdit} onRestored={() => void load()} />
      </div>}
      {activeTab === "images" && <ProductImagesPanel productId={product.id}
        productName={product.product_name_en || product.product_name_jp || product.code || `产品 #${product.id}`}
        readOnly={!canEdit} onImagesChanged={() => void load()} />}
    </div>
    <ProductFormDialog open={editOpen} product={product} categories={categories} suppliers={suppliers} countries={countries} ports={ports}
      onOpenChange={setEditOpen} onSaved={saved => setProduct(saved)} onDuplicate={existing => setProduct(existing)} />
  </main>;
}
