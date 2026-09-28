import { describe, expect, it } from "vitest";

import { normalizeProductPortFilter } from "./product-filter-query";

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
