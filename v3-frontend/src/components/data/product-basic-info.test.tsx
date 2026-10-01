import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ProductItem } from "@/lib/data-api";
import { ProductBasicInfo } from "./product-basic-info";

const product: ProductItem = {
  id: 42,
  revision: 7,
  price_version: 3,
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
  brand: "ORCHARD",
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

describe("ProductBasicInfo", () => {
  it("renders only the existing non-price product fields", () => {
    const html = renderToStaticMarkup(<ProductBasicInfo product={product} />);

    for (const value of [
      "APPLE RED",
      "赤りんご",
      "99PRD010590",
      "ORCHARD",
      "日本",
      "大阪",
      "水果",
      "大阪食品株式会社",
      "KG2.2",
      "2.2kg",
      "40LB",
      "USA",
      "JPY",
      "有效",
    ]) {
      expect(html).toContain(value);
    }
    expect(html).not.toContain("采购价");
    expect(html).not.toContain("卖价");
    expect(html).not.toContain("产品有效开始日期");
    expect(html).not.toContain("产品有效结束日期");
    expect(html).not.toContain(">80<");
    expect(html).not.toContain(">100<");
  });

  it("uses a clear placeholder for nullable values", () => {
    const html = renderToStaticMarkup(
      <ProductBasicInfo
        product={{ ...product, product_name_jp: null, brand: null, is_effective: false }}
      />,
    );

    expect(html).toContain("未填写");
    expect(html).toContain("无效");
  });
});
