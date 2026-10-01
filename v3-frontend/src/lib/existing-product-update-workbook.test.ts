import { beforeEach, describe, expect, it, vi } from "vitest";
import * as XLSX from "xlsx";

import type { ProductItem } from "./data-api";
import {
  buildExistingProductUpdateWorkbook,
  downloadExistingProductUpdateWorkbook,
} from "./existing-product-update-workbook";

vi.mock("xlsx", async () => ({
  ...await vi.importActual<typeof XLSX>("xlsx"),
  writeFile: vi.fn(),
}));

const product = (): ProductItem => ({
  id: 7,
  revision: 4,
  price_version: 2,
  product_name_en: "Synthetic Fish",
  product_name_jp: "合成魚",
  code: "00100",
  price: 80,
  contract_price: 140,
  currency: "JPY",
  country_name: "Japan",
  port_name: "Osaka",
  brand: "Test Brand",
  category_name: "Food",
  supplier_name: "Test Supplier",
  unit: "KG",
  unit_size: "1kg",
  pack_size: "10kg",
  country_of_origin: "Japan",
  price_periods: [
    { id: 11, product_id: 7, price_type: "purchase", amount: 80, currency: "JPY", effective_from: "2026-01-01", effective_to: "2026-03-31", status: true, source: "http", source_batch_id: null, created_by: null, updated_by: null, created_at: null, updated_at: null },
    { id: 12, product_id: 7, price_type: "purchase", amount: 90, currency: "JPY", effective_from: "2026-04-01", effective_to: "2026-06-30", status: true, source: "http", source_batch_id: null, created_by: null, updated_by: null, created_at: null, updated_at: null },
    { id: 21, product_id: 7, price_type: "selling", amount: 140, currency: "JPY", effective_from: "2026-01-01", effective_to: "2026-06-30", status: true, source: "http", source_batch_id: null, created_by: null, updated_by: null, created_at: null, updated_at: null },
  ],
} as ProductItem);

function rows(workbook: XLSX.WorkBook): unknown[][] {
  const bytes = XLSX.write(workbook, { type: "buffer", bookType: "xlsx" });
  const reopened = XLSX.read(bytes, { type: "buffer", cellNF: true });
  return XLSX.utils.sheet_to_json(reopened.Sheets["产品数据"], { header: 1 });
}

beforeEach(() => vi.clearAllMocks());

describe("existing product update workbook", () => {
  it("rejects empty products and an empty scope before writing a file", async () => {
    await expect(buildExistingProductUpdateWorkbook([], { basic: true, purchase: false, selling: false }))
      .rejects.toThrow("请至少选择一个产品");
    await expect(buildExistingProductUpdateWorkbook([product()], { basic: false, purchase: false, selling: false }))
      .rejects.toThrow("请至少选择一个更新范围");
    await expect(downloadExistingProductUpdateWorkbook([], { basic: true, purchase: false, selling: false }))
      .rejects.toThrow("请至少选择一个产品");
    expect(XLSX.writeFile).not.toHaveBeenCalled();
  });

  it("exports only purchase periods for a purchase-only scope", async () => {
    const workbook = await buildExistingProductUpdateWorkbook(
      [product()],
      { basic: false, purchase: true, selling: false },
    );
    const data = rows(workbook);

    expect(data).toHaveLength(4);
    expect(data[1][5]).toBe("00100");
    expect(data[2][14]).toBe(11);
    expect(data[3][14]).toBe(12);
    expect(data.flat()).not.toContain(21);
    expect(data[1].slice(6, 14).every((value) => value == null)).toBe(true);
  });

  it("exports only selling periods for a selling-only scope", async () => {
    const data = rows(await buildExistingProductUpdateWorkbook(
      [product()],
      { basic: false, purchase: false, selling: true },
    ));

    expect(data).toHaveLength(3);
    expect(data[2][15]).toBe(21);
    expect(data.flat()).not.toContain(11);
    expect(data.flat()).not.toContain(12);
  });

  it("exports a single master-data row for a basic-only scope", async () => {
    const data = rows(await buildExistingProductUpdateWorkbook(
      [product()],
      { basic: true, purchase: false, selling: false },
    ));

    expect(data).toHaveLength(2);
    expect(data[1].slice(6, 14)).toEqual([
      "合成魚", "Test Brand", "Food", "Test Supplier", "KG", "1kg", "10kg", "Japan",
    ]);
  });

  it("keeps canonical headers and hides system matching columns", async () => {
    const workbook = await buildExistingProductUpdateWorkbook(
      [product()],
      { basic: true, purchase: true, selling: true },
    );
    const sheet = workbook.Sheets["产品数据"];
    const data = rows(workbook);

    expect(data[0]).toEqual([
      "product_id", "expected_revision", "product_name", "country", "port", "product_code",
      "product_name_jp", "brand", "category", "supplier", "unit", "unit_size",
      "pack_size", "country_of_origin", "purchase_price_period_id", "selling_price_period_id",
      "price", "purchase_price_effective_from", "purchase_price_effective_to",
      "contract_price", "selling_price_effective_from", "selling_price_effective_to", "currency",
    ]);
    expect(sheet["!cols"]?.[0]?.hidden).toBe(true);
    expect(sheet["!cols"]?.[1]?.hidden).toBe(true);
    expect(sheet["!cols"]?.[14]?.hidden).toBe(true);
    expect(sheet["!cols"]?.[15]?.hidden).toBe(true);
  });

  it("writes a stable dated filename only after the workbook is ready", async () => {
    const count = await downloadExistingProductUpdateWorkbook(
      [product()],
      { basic: false, purchase: true, selling: true },
      "2026-10-01",
    );

    expect(count).toBe(1);
    expect(XLSX.writeFile).toHaveBeenCalledWith(
      expect.anything(),
      "已有产品更新_2026-10-01.xlsx",
    );
  });
});
