"use client";

import { useParams, useSearchParams } from "next/navigation";
import { ProductDetailPage } from "@/components/data/product-detail-page";
import { normalizeProductDetailTab } from "@/lib/product-detail-route";

export default function ProductDetailRoute() {
  const params = useParams();
  const searchParams = useSearchParams();
  const productId = Number(params.id);

  return <ProductDetailPage
    productId={productId}
    activeTab={normalizeProductDetailTab(searchParams.get("tab"))}
    initialEdit={searchParams.get("edit") === "1"}
  />;
}
