import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchWithAuth } from "./fetch-with-auth";

vi.mock("./fetch-with-auth", () => ({ fetchWithAuth: vi.fn() }));

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("database setup api", () => {
  beforeEach(() => vi.clearAllMocks());

  it("uses only the fixed database-setup routes", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue(
      json({ enabled: true, database_name: "cruise_v3_clean" }),
    );
    const { getDatabaseSetupStatus } = await import("./database-setup-api");
    await getDatabaseSetupStatus();
    expect(String(vi.mocked(fetchWithAuth).mock.calls[0][0])).toBe(
      "http://localhost:8001/api/database-setup/status",
    );
  });

  it("uploads one workbook with multipart and no database selector", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue(json({ id: "batch-1" }, 201));
    const { uploadDatabaseWorkbook } = await import("./database-setup-api");
    const file = new File(["xlsx"], "products.xlsx");
    await uploadDatabaseWorkbook(file);
    const [url, options] = vi.mocked(fetchWithAuth).mock.calls[0];
    expect(String(url)).toBe("http://localhost:8001/api/database-setup/imports");
    expect(options?.method).toBe("POST");
    expect(options?.body).toBeInstanceOf(FormData);
    expect(String(url)).not.toContain("database=");
  });

  it("checks batch state after an uncertain commit instead of blindly posting twice", async () => {
    vi.mocked(fetchWithAuth)
      .mockRejectedValueOnce(new Error("network lost"))
      .mockResolvedValueOnce(
        json({
          id: "batch-1",
          status: "committed",
          counts: { create: 2, update: 0, skip: 0, warning: 0, block: 0 },
          items: [],
        }),
      );
    const { commitImportWithRecovery } = await import("./database-setup-api");
    const result = await commitImportWithRecovery("batch-1");
    expect(result.status).toBe("committed");
    expect(vi.mocked(fetchWithAuth).mock.calls.map(([url]) => String(url))).toEqual([
      "http://localhost:8001/api/database-setup/imports/batch-1/commit",
      "http://localhost:8001/api/database-setup/imports/batch-1/rows?page=1&page_size=1",
    ]);
  });

  it("sends revisions and exact product-code confirmation for manual changes", async () => {
    vi.mocked(fetchWithAuth).mockImplementation(async () => json({ deleted: true }));
    const { saveSetupProduct, deleteSetupProduct, saveSetupPeriod } = await import("./database-setup-api");
    await saveSetupProduct(7, { fields: [], values: {}, schema_version: 4, expected_revision: 2, extension_revision: 3 }, { brand: "NEW" });
    await deleteSetupProduct(7, { can_delete: true, reasons: [], code: "P7", period_count: 1, expected_revision: 2, expected_extension_revision: 3 }, "P7");
    await saveSetupPeriod(7, "purchase", { amount: 100, currency: "JPY", effective_from: "2027-01-01", effective_to: "2027-01-31" }, { id: 9, product_id: 7, price_type: "purchase", amount: 90, currency: "JPY", effective_from: "2027-01-01", effective_to: "2027-01-31", status: true, revision: 5 });
    const calls = vi.mocked(fetchWithAuth).mock.calls;
    expect(JSON.parse(String(calls[0][1]?.body))).toMatchObject({ expected_revision: 2, extension_revision: 3, schema_version: 4 });
    expect(calls[1][1]?.method).toBe("DELETE");
    expect(JSON.parse(String(calls[1][1]?.body))).toEqual({ expected_revision: 2, expected_extension_revision: 3, confirm_code: "P7" });
    expect(String(calls[2][0])).toContain("/products/7/periods/9");
    expect(JSON.parse(String(calls[2][1]?.body))).toMatchObject({ expected_revision: 5, amount: 100 });
  });
});
