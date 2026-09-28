import { describe, expect, it } from "vitest";

import {
  getProductPortFilterParams,
  normalizeProductPortFilter,
} from "./product-filter-query";

describe("normalizeProductPortFilter", () => {
  it.each([
    ["19", 19],
    ["20", 20],
  ])("keeps a positive port ID %s", (value, expected) => {
    expect(normalizeProductPortFilter(value)).toBe(expected);
  });

  it.each(["all", "", "not-an-id", "1.5", "0", "-2", " 19 "])(
    "omits an invalid or unselected value %s",
    (value) => {
      expect(normalizeProductPortFilter(value)).toBeUndefined();
    },
  );
});

describe("getProductPortFilterParams", () => {
  it("returns the shared list/export query fragment for a selected port", () => {
    expect(getProductPortFilterParams("19")).toEqual({ port_id: 19 });
  });

  it("omits port_id when all ports are selected", () => {
    expect(getProductPortFilterParams("all")).toEqual({});
  });
});
