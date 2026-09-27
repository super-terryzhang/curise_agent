import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchWithAuth } from "./fetch-with-auth";
import { listProducts } from "./data-api";

vi.mock("./fetch-with-auth", () => ({ fetchWithAuth: vi.fn() }));

beforeEach(() => vi.clearAllMocks());

describe("product list API", () => {
  it("sends the selected server-side sort with pagination", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue(
      new Response(JSON.stringify({ total: 0, items: [] }), { status: 200 }),
    );

    await listProducts({ sort: "name_asc", limit: 24, offset: 48 });

    const url = String(vi.mocked(fetchWithAuth).mock.calls[0][0]);
    expect(url).toBe(
      "http://localhost:8001/api/data/products?sort=name_asc&limit=24&offset=48",
    );
  });
});
