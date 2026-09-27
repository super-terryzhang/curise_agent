import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ProductItem } from "@/lib/data-api";
import {
  ProductGalleryGrid,
  ProductViewToggle,
} from "./product-gallery-grid";

const product = (
  id: number,
  overrides: Partial<ProductItem> = {},
): ProductItem => ({
  id,
  revision: 1,
  price_version: 1,
  product_name_en: `Product ${id}`,
  product_name_jp: null,
  code: `SKU-${id}`,
  unit: "EA",
  price: 3.25,
  contract_price: 4.4,
  thumbnail_url: "https://example.com/product.jpg",
  image_count: 1,
  unit_size: null,
  pack_size: null,
  country_of_origin: null,
  brand: "Example Brand",
  currency: "USD",
  status: true,
  country_name: "Japan",
  category_name: "Drinks",
  supplier_name: "Example Supplier",
  port_name: "東京",
  country_id: 1,
  category_id: 2,
  supplier_id: 3,
  port_id: 4,
  effective_from: null,
  effective_to: null,
  is_effective: true,
  ...overrides,
});

const callbacks = {
  onPageChange: () => undefined,
  onOpenImages: () => undefined,
  onOpenHistory: () => undefined,
  onManagePrices: () => undefined,
  onEdit: () => undefined,
  onToggleStatus: () => undefined,
  onDelete: () => undefined,
};

describe("product gallery grid", () => {
  it("renders image-led product cards with selling price, status and paging", () => {
    const html = renderToStaticMarkup(
      <ProductGalleryGrid
        products={[
          product(1, {
            product_name_en: "Chamisul Original",
            product_name_jp: "チャミスル",
          }),
          product(2, {
            product_name_en: "No Image Product",
            thumbnail_url: null,
            image_count: 0,
          }),
        ]}
        totalProducts={50}
        pageIndex={1}
        pageSize={24}
        isWriter
        {...callbacks}
      />,
    );

    expect(html).toContain('aria-label="产品图库"');
    expect(html).toContain('src="https://example.com/product.jpg"');
    expect(html).toContain('alt="Chamisul Original"');
    expect(html).toContain("チャミスル");
    expect(html).toContain("Example Brand · 東京");
    expect(html).toContain("USD 4.40");
    expect(html).toContain("有效");
    expect(html).toContain("上传图片");
    expect(html).toContain("2 / 3");
    expect(html).toContain('aria-label="上一页"');
    expect(html).toContain('aria-label="下一页"');
  });

  it("shows truthful read-only empty-image, price and effective-state labels", () => {
    const html = renderToStaticMarkup(
      <ProductGalleryGrid
        products={[
          product(3, {
            thumbnail_url: null,
            image_count: 0,
            contract_price: null,
            status: true,
            is_effective: false,
            brand: null,
            port_name: null,
          }),
        ]}
        totalProducts={1}
        pageIndex={0}
        pageSize={24}
        isWriter={false}
        {...callbacks}
      />,
    );

    expect(html).toContain("暂无图片");
    expect(html).not.toContain("上传图片");
    expect(html).toContain("卖价未配置");
    expect(html).toContain("品牌未填写 · 港口未填写");
    expect(html).toContain("无效");
  });

  it("marks the selected product view accessibly", () => {
    const html = renderToStaticMarkup(
      <ProductViewToggle view="gallery" onViewChange={() => undefined} />,
    );

    expect(html).toContain("列表视图");
    expect(html).toContain("图库视图");
    expect(html).toMatch(/aria-pressed="true"[^>]*>[^<]*<svg[^>]*>/);
  });

  it("keeps recovery pagination visible when the current page is empty", () => {
    const html = renderToStaticMarkup(
      <ProductGalleryGrid
        products={[]}
        totalProducts={25}
        pageIndex={1}
        pageSize={24}
        isWriter={false}
        {...callbacks}
      />,
    );

    expect(html).toContain("暂无产品数据");
    expect(html).toContain("2 / 2");
    expect(html).toContain('aria-label="上一页"');
  });
});
