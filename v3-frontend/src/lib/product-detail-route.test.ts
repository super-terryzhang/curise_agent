import { describe, expect, it } from "vitest";
import {
  legacyProductDetailHref,
  normalizeProductDetailTab,
  productDetailHref,
} from "./product-detail-route";

describe("product detail routes", () => {
  it.each([
    ["basic", "basic"],
    ["prices", "prices"],
    ["images", "images"],
    ["unknown", "basic"],
    [null, "basic"],
  ] as const)("normalizes tab %s", (input, expected) => {
    expect(normalizeProductDetailTab(input)).toBe(expected);
  });

  it("builds stable product detail links", () => {
    expect(productDetailHref(42)).toBe("/dashboard/data/products/42?tab=basic");
    expect(productDetailHref(42, { tab: "prices" })).toBe("/dashboard/data/products/42?tab=prices");
    expect(productDetailHref(42, { tab: "basic", edit: true })).toBe(
      "/dashboard/data/products/42?tab=basic&edit=1",
    );
  });

  it("maps legacy product/action query links without losing intent", () => {
    expect(legacyProductDetailHref("42", "prices")).toBe("/dashboard/data/products/42?tab=prices");
    expect(legacyProductDetailHref("42", "edit")).toBe("/dashboard/data/products/42?tab=basic&edit=1");
    expect(legacyProductDetailHref("42", null)).toBe("/dashboard/data/products/42?tab=basic");
    expect(legacyProductDetailHref("not-an-id", "edit")).toBeNull();
  });
});
