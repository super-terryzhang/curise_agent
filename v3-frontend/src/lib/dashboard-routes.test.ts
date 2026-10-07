import { describe, expect, it } from "vitest";

import {
  canonicalizeDashboardPath,
  DATA_TABLES_PATH,
  isPathWithin,
  LEGACY_DATA_TABLES_PATH,
} from "./dashboard-routes";

describe("dashboard route boundaries", () => {
  it("matches an exact route and its real descendants", () => {
    expect(isPathWithin("/dashboard/settings", "/dashboard/settings")).toBe(true);
    expect(isPathWithin("/dashboard/settings/company", "/dashboard/settings")).toBe(true);
  });

  it("does not treat a similar prefix as a descendant", () => {
    expect(isPathWithin(DATA_TABLES_PATH, "/dashboard/data")).toBe(false);
    expect(isPathWithin("/dashboard/settings-other", "/dashboard/settings")).toBe(false);
  });

  it("canonicalizes legacy data-table list and detail paths", () => {
    expect(canonicalizeDashboardPath(LEGACY_DATA_TABLES_PATH)).toBe(DATA_TABLES_PATH);
    expect(
      canonicalizeDashboardPath(`${LEGACY_DATA_TABLES_PATH}/table-1`),
    ).toBe(`${DATA_TABLES_PATH}/table-1`);
  });

  it("leaves unrelated settings paths unchanged", () => {
    expect(canonicalizeDashboardPath("/dashboard/settings/company")).toBe(
      "/dashboard/settings/company",
    );
    expect(canonicalizeDashboardPath("/dashboard/settings/data-tables-old")).toBe(
      "/dashboard/settings/data-tables-old",
    );
  });
});
