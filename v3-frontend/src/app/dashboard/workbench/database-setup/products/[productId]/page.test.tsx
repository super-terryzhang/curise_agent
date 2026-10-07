import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ProductDetailView } from "./page";

describe("clean database product detail", () => {
  it("separates and chronologically orders purchase and selling periods", () => {
    const html = renderToStaticMarkup(
      <ProductDetailView
        today="2027-03-15"
        product={{
          id: 1, code: "P-1", name: "APPLE", port: "大阪", country: "日本",
          supplier: "松武", unit: "KG", category: "青果", brand: "TEST",
          status: true, revision: 1, schema_version: 7,
          extensions: [{ label: "业务分类", type: "single_select", value: "winner" }],
          price_periods: [
            { id: 2, product_id: 1, price_type: "purchase", amount: 120, currency: "JPY", effective_from: "2027-04-01", effective_to: "2027-04-30", status: true },
            { id: 1, product_id: 1, price_type: "purchase", amount: 100, currency: "JPY", effective_from: "2027-01-01", effective_to: "2027-01-31", status: true },
            { id: 3, product_id: 1, price_type: "selling", amount: 180, currency: "JPY", effective_from: "2027-03-01", effective_to: "2027-03-31", status: true },
          ],
        }}
      />,
    );
    expect(html).toContain("采购价区间");
    expect(html).toContain("卖价区间");
    expect(html.indexOf("2027-01-01")).toBeLessThan(html.indexOf("2027-04-01"));
    expect(html).toContain("已结束");
    expect(html).toContain("当前");
    expect(html).toContain("未来");
    expect(html).toContain("编辑产品资料");
    expect(html).toContain("新增采购价区间");
    expect(html).toContain("新增卖价区间");
    expect(html).toContain("删除区间");
  });
});
