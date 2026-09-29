import { describe, expect, it } from "vitest";
import type { ProductItem } from "@/lib/data-api";
import {
  emptyProductForm,
  productFormPayload,
  productToForm,
  validateProductForm,
} from "./product-form-dialog";

const product: ProductItem = {
  id: 42,
  revision: 7,
  price_version: 3,
  product_name_en: "APPLE RED",
  product_name_jp: "赤りんご",
  code: "99PRD010590",
  unit: "KG2.2",
  price: 80,
  contract_price: 0,
  profit_margin: null,
  purchase_price_effective_from: "2026-01-01T00:00:00",
  purchase_price_effective_to: "2026-03-31T00:00:00",
  selling_price_effective_from: "2026-01-05T00:00:00",
  selling_price_effective_to: "2026-04-01T00:00:00",
  thumbnail_url: null,
  image_count: 0,
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
  effective_from: "2026-01-01T00:00:00",
  effective_to: "2026-12-31T00:00:00",
  is_effective: true,
};

describe("product form mapping", () => {
  it("maps an existing product without losing zero prices or date values", () => {
    const form = productToForm(product);

    expect(form.contract_price).toBe("0");
    expect(form.purchase_price_effective_from).toBe("2026-01-01");
    expect(form.selling_price_effective_to).toBe("2026-04-01");
    expect(form.supplier_id).toBe("3");
  });

  it("keeps cleared edit fields explicit and includes optimistic-lock revision", () => {
    const payload = productFormPayload(
      { ...emptyProductForm, product_name_en: "  APPLE RED  ", contract_price: "0" },
      product,
    );

    expect(payload).toMatchObject({
      product_name_en: "APPLE RED",
      product_name_jp: null,
      country_id: null,
      price: null,
      contract_price: 0,
      expected_revision: 7,
    });
  });

  it("omits empty optional values for create payloads", () => {
    const payload = productFormPayload(
      { ...emptyProductForm, product_name_en: "NEW PRODUCT" },
      null,
    );

    expect(payload.product_name_en).toBe("NEW PRODUCT");
    expect(JSON.stringify(payload)).not.toContain("product_name_jp");
    expect(JSON.stringify(payload)).not.toContain("expected_revision");
  });
});

describe("validateProductForm", () => {
  it("requires an English product name", () => {
    expect(validateProductForm(emptyProductForm)).toBe("英文品名不能为空");
  });

  it.each([
    ["purchase", "采购价有效开始日期不能晚于结束日期"],
    ["selling", "卖价有效开始日期不能晚于结束日期"],
  ] as const)("rejects a reversed %s price interval", (kind, expected) => {
    const form = {
      ...emptyProductForm,
      product_name_en: "APPLE",
      ...(kind === "purchase"
        ? { purchase_price_effective_from: "2026-04-01", purchase_price_effective_to: "2026-03-31" }
        : { selling_price_effective_from: "2026-04-01", selling_price_effective_to: "2026-03-31" }),
    };

    expect(validateProductForm(form)).toBe(expected);
  });
});
