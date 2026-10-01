import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchWithAuth } from "./fetch-with-auth";
import { loadAllProductBatchRows } from "./product-upload-api";

vi.mock("./fetch-with-auth", () => ({ fetchWithAuth: vi.fn() }));

function response(page: number, totalPages: number, ids: number[]) {
  return new Response(JSON.stringify({
    items: ids.map((id) => ({
      staging_id: id,
      source_row_number: id + 1,
      kind: "update",
      identity: { product_id: id, product_code: String(id), product_name: `P${id}` },
      operations: ["更新产品"],
      fields: [],
      issues: [],
      reason: null,
    })),
    page,
    page_size: 200,
    total_items: 3,
    total_pages: totalPages,
    summary: { create: 0, update: 3, skip: 0, error: 0, total: 3 },
  }), { status: 200, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => vi.clearAllMocks());

describe("loadAllProductBatchRows", () => {
  it("loads every server page exactly once and preserves row order", async () => {
    vi.mocked(fetchWithAuth)
      .mockResolvedValueOnce(response(1, 2, [1, 2]))
      .mockResolvedValueOnce(response(2, 2, [3]));

    const result = await loadAllProductBatchRows(42, "changes");

    expect(result.items.map((item) => item.staging_id)).toEqual([1, 2, 3]);
    expect(fetchWithAuth).toHaveBeenCalledTimes(2);
    expect(String(vi.mocked(fetchWithAuth).mock.calls[1][0])).toContain("page=2");
    expect(String(vi.mocked(fetchWithAuth).mock.calls[1][0])).toContain("page_size=200");
  });
});
