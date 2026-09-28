import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  calculateProductProfitMargin,
  ProductProfitMargin,
} from "./product-profit-margin";

describe("calculateProductProfitMargin", () => {
  it.each([
    [80, 100, 20],
    ["80", "100", 20],
    [100, 100, 0],
    [100, 80, -25],
    [0, 100, 100],
    [10, 30, 66.67],
  ])("calculates purchase %s and selling %s", (purchase, selling, expected) => {
    expect(calculateProductProfitMargin(purchase, selling)).toBe(expected);
  });

  it.each([
    ["", "100"],
    ["   ", "100"],
    [null, 100],
    [80, undefined],
    ["not-a-price", 100],
    [Number.POSITIVE_INFINITY, 100],
    [80, Number.NaN],
    [80, 0],
    [80, -1],
    [-1, 100],
  ])("returns null for invalid purchase %s or selling %s", (purchase, selling) => {
    expect(calculateProductProfitMargin(purchase, selling)).toBeNull();
  });
});

describe("ProductProfitMargin", () => {
  it.each([
    [20, "20.00%", "text-emerald-600"],
    [0, "0.00%", "text-foreground"],
    [-25, "-25.00%", "text-red-600"],
    [null, "未配置", "text-muted-foreground"],
  ] as const)("renders %s with semantic styling", (value, label, className) => {
    const html = renderToStaticMarkup(<ProductProfitMargin value={value} />);

    expect(html).toContain(label);
    expect(html).toContain(className);
  });

  it("supports a contextual empty label", () => {
    const html = renderToStaticMarkup(
      <ProductProfitMargin value={null} emptyLabel="利润率未配置" />,
    );

    expect(html).toContain("利润率未配置");
  });
});
