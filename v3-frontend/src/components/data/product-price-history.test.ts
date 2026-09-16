import { describe, expect, it, vi } from "vitest";
import { priceText } from "./product-price-history";
import { fetchWithAuth } from "@/lib/fetch-with-auth";
import { getProductPriceHistory, restoreProductPrice } from "@/lib/data-api";
vi.mock("@/lib/fetch-with-auth", () => ({ fetchWithAuth: vi.fn() }));

describe("product ledger user contract", () => {
  it("distinguishes zero, missing price, and unrecorded legacy fields", () => {
    const snapshot = { id: 42, price: "0.00", contract_price: null, currency: "JPY", unit: "KG" };
    expect(priceText(snapshot, "price")).toBe("0.00 JPY / KG");
    expect(priceText(snapshot, "contract_price")).toBe("未设置");
    expect(priceText({ ...snapshot, known_fields: ["price"] }, "contract_price")).toBe("未记录");
    expect(priceText({ ...snapshot, currency: null }, "price")).toContain("币种未记录");
    expect(priceText(null, "price")).toBe("—");
  });
  it("preserves version zero and product scope in paging and restore requests", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue(new Response("{}", { status: 200 }));
    await getProductPriceHistory(42, { max_version: 0, offset: 10, legacy: false, field: "price" });
    const url = String(vi.mocked(fetchWithAuth).mock.calls.at(-1)![0]);
    expect(url).toContain("/products/42/price-history?");
    expect(url).toContain("max_version=0");
    expect(url).toContain("offset=10");
    vi.mocked(fetchWithAuth).mockResolvedValue(new Response("{}", { status: 200 }));
    await restoreProductPrice(42, "a".repeat(32), 7);
    const call = vi.mocked(fetchWithAuth).mock.calls.at(-1)!;
    expect(String(call[0])).toContain("/products/42/price-history/restore");
    expect(JSON.parse(String(call[1]?.body))).toEqual({ event_id: "a".repeat(32), expected_revision: 7 });
  });
  it("surfaces a stale-revision error rather than reporting success", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue(new Response(JSON.stringify({ detail: "产品已被修改，请刷新" }), { status: 409 }));
    await expect(restoreProductPrice(42, "a".repeat(32), 7)).rejects.toThrow("产品已被修改");
  });
});
