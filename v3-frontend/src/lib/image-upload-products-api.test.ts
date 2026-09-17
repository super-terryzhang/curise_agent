import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchWithAuth } from "./fetch-with-auth";
import { listImageUploadProducts } from "./data-api";

vi.mock("./fetch-with-auth", () => ({ fetchWithAuth: vi.fn() }));

beforeEach(() => vi.clearAllMocks());

describe("image upload product options API", () => {
  it("sends only the product-table filters and pagination", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue(
      new Response(JSON.stringify({ total: 0, items: [] }), { status: 200 }),
    );

    await listImageUploadProducts({
      search: "99PRD80671",
      country_id: 3,
      port_id: 8,
      only_without_images: true,
      limit: 40,
      offset: 80,
    });

    const url = String(vi.mocked(fetchWithAuth).mock.calls[0][0]);
    expect(url).toBe(
      "http://localhost:8001/api/data/products/image-upload-options?search=99PRD80671&country_id=3&port_id=8&only_without_images=true&limit=40&offset=80",
    );
  });
});
