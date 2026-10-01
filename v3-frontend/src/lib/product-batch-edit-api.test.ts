import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchWithAuth } from "./fetch-with-auth";
import { commitDirectProductUpdate } from "./product-batch-edit-api";
import { rollbackProductBatch } from "./product-upload-api";
vi.mock("./fetch-with-auth", () => ({ fetchWithAuth: vi.fn() }));
const reply = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
beforeEach(() => vi.clearAllMocks());
describe("direct save response recovery", () => {
  it("does not report a conflicted rollback as success", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValueOnce(
      reply({ skipped: 1, conflicts: [{ reason: "存在后续修改" }] }),
    );
    await expect(rollbackProductBatch(12)).rejects.toThrow("存在后续修改");
  });
  it("recovers completion when save succeeded but its response was lost", async () => {
    vi.mocked(fetchWithAuth)
      .mockRejectedValueOnce(new Error("network lost"))
      .mockResolvedValueOnce(
        reply({
          status: "completed",
          summary: { create: 0, update: 2, skip: 0, error: 0 },
        }),
      );
    expect(await commitDirectProductUpdate(12)).toEqual({
      created: 0,
      updated: 2,
      skipped: 0,
      errors: 0,
      error_details: [],
    });
    expect(fetchWithAuth).toHaveBeenCalledTimes(2);
    expect(vi.mocked(fetchWithAuth).mock.calls[1][0]).toContain(
      "/workbench/batches/12",
    );
  });
  it("keeps genuine unresolved save failures visible and does not retry writes", async () => {
    vi.mocked(fetchWithAuth)
      .mockResolvedValueOnce(reply({ detail: "产品已被他人更新" }, 409))
      .mockResolvedValueOnce(reply({ status: "resolved" }));
    await expect(commitDirectProductUpdate(12)).rejects.toThrow(
      "产品已被他人更新",
    );
    expect(fetchWithAuth).toHaveBeenCalledTimes(2);
  });
});
