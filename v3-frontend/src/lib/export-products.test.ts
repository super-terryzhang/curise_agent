import { beforeEach, describe, expect, it, vi } from "vitest";
import * as XLSX from "xlsx";
import { listProducts, type ProductItem } from "./data-api";
import { buildPriceWorkbook, exportProductPrices, loadProductsForExport } from "./export-products";

vi.mock("./data-api", () => ({ listProducts: vi.fn() }));
vi.mock("xlsx", async () => ({ ...await vi.importActual<typeof XLSX>("xlsx"), writeFile: vi.fn() }));

const product = (id: number): ProductItem => ({
  id, product_name_en: `Synthetic ${id}`, country_name: "TestCountry", port_name: "TestPort",
  code: String(id).padStart(5, "0"), price: 100.25,
  purchase_price_effective_from: "2026-01-01T00:00:00",
  purchase_price_effective_to: "2026-06-30T00:00:00",
  contract_price: 150.5,
  selling_price_effective_from: "2026-02-01T00:00:00",
  selling_price_effective_to: "2026-12-31T00:00:00",
  currency: "JPY",
} as ProductItem);

beforeEach(() => vi.clearAllMocks());

describe("product price export", () => {
  it("fetches every filtered page, ignoring the visible page size", async () => {
    const first = Array.from({ length: 500 }, (_, i) => product(i + 1));
    vi.mocked(listProducts).mockResolvedValueOnce({ items: first, total: 501 })
      .mockResolvedValueOnce({ items: [product(501)], total: 501 });
    const rows = await loadProductsForExport({ search: "Synthetic", country_id: 2, is_effective: false });
    expect(rows).toHaveLength(501);
    expect(listProducts).toHaveBeenNthCalledWith(2, {
      search: "Synthetic", country_id: 2, is_effective: false, limit: 500, offset: 500,
    });
  });

  it("does not download a partial workbook when a later request fails", async () => {
    vi.mocked(listProducts).mockResolvedValueOnce({ items: [product(1)], total: 2 })
      .mockRejectedValueOnce(new Error("network failure"));
    await expect(exportProductPrices({})).rejects.toThrow("network failure");
    expect(XLSX.writeFile).not.toHaveBeenCalled();
  });

  it("rejects changing totals and duplicate pages", async () => {
    vi.mocked(listProducts).mockResolvedValueOnce({ items: [product(1)], total: 2 })
      .mockResolvedValueOnce({ items: [product(2)], total: 3 });
    await expect(loadProductsForExport({})).rejects.toThrow("数量发生变化");
    vi.mocked(listProducts).mockResolvedValueOnce({ items: [product(1)], total: 2 })
      .mockResolvedValueOnce({ items: [product(1)], total: 2 });
    await expect(loadProductsForExport({})).rejects.toThrow("列表发生变化");
  });

  it("exports both prices, identity and typed leading-zero codes in a real xlsx", async () => {
    const workbook = await buildPriceWorkbook([product(100)]);
    const bytes = XLSX.write(workbook, { type: "buffer", bookType: "xlsx" });
    const reopened = XLSX.read(bytes, { type: "buffer", cellNF: true });
    const sheet = reopened.Sheets["产品数据"];
    expect(XLSX.utils.sheet_to_json(sheet, { header: 1 })[0]).toEqual([
      "product_name", "country", "port", "product_code", "price",
      "purchase_price_effective_from", "purchase_price_effective_to",
      "contract_price", "selling_price_effective_from", "selling_price_effective_to",
      "currency",
    ]);
    expect(sheet.D2).toMatchObject({ t: "s", v: "00100", z: "@" });
    expect(sheet.E2).toMatchObject({ t: "n", v: 100.25 });
    expect(sheet.F2).toMatchObject({ t: "n", z: "yyyy-mm-dd" });
    expect(sheet.G2).toMatchObject({ t: "n", z: "yyyy-mm-dd" });
    expect(sheet.H2).toMatchObject({ t: "n", v: 150.5 });
    expect(sheet.I2).toMatchObject({ t: "n", z: "yyyy-mm-dd" });
    expect(sheet.J2).toMatchObject({ t: "n", z: "yyyy-mm-dd" });
    // Persist only synthetic data for the backend parser round-trip verification.
    if (process.env.PRICING_EXPORT_FIXTURE) {
      const fs = await import("node:fs/promises");
      await fs.writeFile(process.env.PRICING_EXPORT_FIXTURE, bytes);
    }
  });

  it("downloads only after all rows are collected", async () => {
    vi.mocked(listProducts).mockResolvedValueOnce({ items: [product(1)], total: 1 });
    expect(await exportProductPrices({})).toBe(1);
    expect(XLSX.writeFile).toHaveBeenCalledWith(expect.anything(), expect.stringMatching(/\.xlsx$/));
  });

  it("exports multiple canonical periods as separate rows", async () => {
    const item = product(7);
    item.price_periods = [
      { id: 1, product_id: 7, price_type: "purchase", amount: 80, currency: "JPY", effective_from: "2026-01-01", effective_to: "2026-06-30", status: true, source: "http", source_batch_id: null, created_by: null, updated_by: null, created_at: null, updated_at: null },
      { id: 2, product_id: 7, price_type: "purchase", amount: 90, currency: "JPY", effective_from: "2026-07-01", effective_to: "2026-12-31", status: true, source: "http", source_batch_id: null, created_by: null, updated_by: null, created_at: null, updated_at: null },
      { id: 3, product_id: 7, price_type: "selling", amount: 140, currency: "JPY", effective_from: "2026-01-01", effective_to: "2026-12-31", status: true, source: "http", source_batch_id: null, created_by: null, updated_by: null, created_at: null, updated_at: null },
    ];
    const workbook = await buildPriceWorkbook([item]);
    const sheet = workbook.Sheets["产品数据"];
    const rows = XLSX.utils.sheet_to_json<unknown[]>(sheet, { header: 1 });
    expect(rows).toHaveLength(4);
    expect(sheet.E2.v).toBe(80);
    expect(sheet.E3.v).toBe(90);
    expect(sheet.H4.v).toBe(140);
  });

  it("refuses an empty export", async () => {
    vi.mocked(listProducts).mockResolvedValueOnce({ items: [], total: 0 });
    await expect(exportProductPrices({})).rejects.toThrow("没有可导出的产品");
    expect(XLSX.writeFile).not.toHaveBeenCalled();
  });
});
