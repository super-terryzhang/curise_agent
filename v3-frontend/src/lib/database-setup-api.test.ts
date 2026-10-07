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
});
