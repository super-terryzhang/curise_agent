import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { ProductItem } from "@/lib/data-api";
import {
  ProductDetailHeader,
  ProductDetailLoadState,
  ProductDetailTabs,
} from "./product-detail-page";

const product: ProductItem = {
  id: 42,
  revision: 1,
  price_version: 1,
  product_name_en: "APPLE RED",
  product_name_jp: "赤りんご",
  code: "99PRD010590",
  unit: "KG2.2",
  price: 80,
  contract_price: 100,
  profit_margin: 20,
  thumbnail_url: null,
  image_count: 2,
  unit_size: "2.2kg",
  pack_size: "40LB",
  country_of_origin: "USA",
  brand: null,
  currency: "JPY",
  status: true,
  country_name: "日本",
  category_name: "水果",
  supplier_name: "大阪食品株式会社",
  port_name: "大阪",
  country_id: 1,
  category_id: 2,
  supplier_id: 3,
  port_id: 4,
  is_effective: true,
};

describe("product detail page presentation", () => {
  it("shows a stable header and edit control only to writers", () => {
    const writer = renderToStaticMarkup(
      <ProductDetailHeader product={product} canEdit onEdit={vi.fn()} />,
    );
    const reader = renderToStaticMarkup(
      <ProductDetailHeader product={product} canEdit={false} onEdit={vi.fn()} />,
    );

    expect(writer).toContain("APPLE RED");
    expect(writer).toContain("99PRD010590");
    expect(writer).toContain("编辑产品");
    expect(reader).not.toContain("编辑产品");
  });

  it("links the three detail sections and marks the active one", () => {
    const html = renderToStaticMarkup(<ProductDetailTabs productId={42} activeTab="prices" />);

    expect(html).toContain("基本信息");
    expect(html).toContain("价格历史");
    expect(html).toContain("产品图片");
    expect(html).toContain('/dashboard/data/products/42?tab=prices');
    expect(html).toContain('aria-current="page"');
  });

  it("distinguishes not-found from recoverable load errors", () => {
    const notFound = renderToStaticMarkup(<ProductDetailLoadState loading={false} error="产品不存在" />);
    const failed = renderToStaticMarkup(<ProductDetailLoadState loading={false} error="网络异常" />);

    expect(notFound).toContain("找不到这个产品");
    expect(failed).toContain("产品加载失败");
    expect(failed).toContain("网络异常");
    expect(failed).toContain("返回产品列表");
  });
});
