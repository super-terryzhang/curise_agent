import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { ProductItem, ProductPricePeriod } from "@/lib/data-api";
import {
  ProductPricePeriodGroups,
  ProductPricePeriodsPanel,
} from "./product-price-periods";
import {
  ProductPriceHistoryPanel,
  ProductPriceHistoryStatus,
} from "./product-price-history";

const product = {
  id: 42,
  revision: 1,
  price_version: 1,
  product_name_en: "APPLE RED",
  product_name_jp: null,
  code: "APPLE",
  unit: "KG",
  price: null,
  contract_price: null,
  profit_margin: null,
  currency: "JPY",
  price_periods: [],
  thumbnail_url: null,
  image_count: 0,
  unit_size: null,
  pack_size: null,
  country_of_origin: null,
  brand: null,
  status: true,
  country_name: null,
  category_name: null,
  supplier_name: null,
  port_name: null,
  country_id: null,
  category_id: null,
  supplier_id: null,
  port_id: null,
  effective_from: null,
  effective_to: null,
} satisfies ProductItem;

const periods: ProductPricePeriod[] = [
  {
    id: 1,
    product_id: 42,
    price_type: "purchase",
    amount: 80,
    currency: "JPY",
    effective_from: "2026-01-01",
    effective_to: "2026-03-31",
    status: true,
    source: "http",
    source_batch_id: null,
    created_by: 1,
    updated_by: 1,
    created_at: null,
    updated_at: null,
  },
  {
    id: 2,
    product_id: 42,
    price_type: "selling",
    amount: 100,
    currency: "JPY",
    effective_from: "2026-04-01",
    effective_to: "2026-04-30",
    status: true,
    source: "http",
    source_batch_id: null,
    created_by: 1,
    updated_by: 1,
    created_at: null,
    updated_at: null,
  },
];

describe("ProductPricePeriodGroups", () => {
  it("keeps purchase and selling periods in separate sections", () => {
    const html = renderToStaticMarkup(
      <ProductPricePeriodGroups
        periods={periods}
        canEdit
        onEdit={vi.fn()}
        onDeactivate={vi.fn()}
      />,
    );

    expect(html).toContain("采购价区间");
    expect(html).toContain("JPY 80");
    expect(html).toContain("卖价区间");
    expect(html).toContain("JPY 100");
    expect(html.match(/编辑/g)).toHaveLength(2);
  });

  it("shows independent empty states and suppresses mutations for read-only users", () => {
    const html = renderToStaticMarkup(
      <ProductPricePeriodGroups periods={[]} canEdit={false} onEdit={vi.fn()} onDeactivate={vi.fn()} />,
    );

    expect(html).toContain("尚未配置采购价区间");
    expect(html).toContain("尚未配置卖价区间");
    expect(html).not.toContain("编辑");
    expect(html).not.toContain("停用");
  });
});

describe("embedded price panels", () => {
  it("renders an independently loadable interval panel without a dialog shell", () => {
    const html = renderToStaticMarkup(
      <ProductPricePeriodsPanel product={product} canEdit={false} onChanged={vi.fn()} />,
    );

    expect(html).toContain("价格区间");
    expect(html).toContain("正在加载价格区间");
    expect(html).not.toContain("role=\"dialog\"");
  });

  it("keeps the price-history filters and refresh control in the embedded panel", () => {
    const html = renderToStaticMarkup(
      <ProductPriceHistoryPanel product={product} canRestore={false} onRestored={vi.fn()} />,
    );

    expect(html).toContain("记录范围");
    expect(html).toContain("筛选价格类型");
    expect(html).toContain("开始日期");
    expect(html).toContain("结束日期");
    expect(html).toContain("刷新");
    expect(html).toContain("正在加载价格历史");
    expect(html).not.toContain("恢复为此价格");
    expect(html).not.toContain("role=\"dialog\"");
  });

  it("renders explicit history error and empty states", () => {
    const errorHtml = renderToStaticMarkup(
      <ProductPriceHistoryStatus loading={false} error="网络异常" data={null} />,
    );
    const emptyHtml = renderToStaticMarkup(
      <ProductPriceHistoryStatus
        loading={false}
        error=""
        data={{
          product_id: 42,
          current: null,
          revision: 1,
          deleted: false,
          total: 0,
          change_count: 0,
          max_version: 0,
          items: [],
          legacy_count: 0,
          has_more: false,
        }}
      />,
    );

    expect(errorHtml).toContain("网络异常，请刷新重试");
    expect(emptyHtml).toContain("此筛选范围内没有价格记录");
  });
});
