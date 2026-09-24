import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchWithAuth } from "./fetch-with-auth";
import {
  listUnitConversionRules,
  retireUnitConversionRule,
  verifyUnitConversionRule,
} from "./data-api";

vi.mock("./fetch-with-auth", () => ({ fetchWithAuth: vi.fn() }));

beforeEach(() => vi.clearAllMocks());

describe("unit conversion rules API", () => {
  it("lists rules with the selected status and product", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200 }),
    );

    await listUnitConversionRules({ status: "draft", product_id: 1820 });

    expect(String(vi.mocked(fetchWithAuth).mock.calls[0][0])).toBe(
      "http://localhost:8001/api/data/unit-conversion-rules?status=draft&product_id=1820",
    );
  });

  it("sends the revision and new evidence when verifying", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue(
      new Response(JSON.stringify({ id: 7, status: "verified" }), { status: 200 }),
    );

    await verifyUnitConversionRule(7, {
      expected_revision: 3,
      evidence: "供应商书面确认",
    });

    const [, options] = vi.mocked(fetchWithAuth).mock.calls[0];
    expect(options).toMatchObject({
      method: "PATCH",
      body: JSON.stringify({
        expected_revision: 3,
        evidence: "供应商书面确认",
      }),
    });
  });

  it("sends the current revision when retiring", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue(
      new Response(JSON.stringify({ id: 7, status: "retired" }), { status: 200 }),
    );

    await retireUnitConversionRule(7, {
      expected_revision: 4,
      evidence: "包装规格已经变更",
    });

    const [url, options] = vi.mocked(fetchWithAuth).mock.calls[0];
    expect(String(url)).toBe(
      "http://localhost:8001/api/data/unit-conversion-rules/7/retire",
    );
    expect(options).toMatchObject({
      method: "PATCH",
      body: JSON.stringify({
        expected_revision: 4,
        evidence: "包装规格已经变更",
      }),
    });
  });
});
