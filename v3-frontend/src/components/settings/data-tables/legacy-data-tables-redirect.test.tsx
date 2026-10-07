// @vitest-environment jsdom
import React from "react";
import "@/test/setup-dom";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { LegacyDataTablesRedirect } from "./legacy-data-tables-redirect";

const navigation = vi.hoisted(() => ({
  replace: vi.fn(),
  query: new URLSearchParams(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: navigation.replace }),
  useSearchParams: () => navigation.query,
}));

beforeEach(() => {
  navigation.replace.mockReset();
  navigation.query = new URLSearchParams();
});

it("preserves filters when redirecting the legacy list route", async () => {
  navigation.query = new URLSearchParams("status=archived&page=2");

  render(<LegacyDataTablesRedirect />);

  expect(screen.getByRole("status").textContent).toContain("正在转到数据表管理");
  await waitFor(() =>
    expect(navigation.replace).toHaveBeenCalledWith(
      "/dashboard/data-tables?status=archived&page=2",
    ),
  );
  expect(navigation.replace).toHaveBeenCalledTimes(1);
});

it("preserves the table id and record query on legacy detail routes", async () => {
  navigation.query = new URLSearchParams("tab=records&record=row-2");

  render(<LegacyDataTablesRedirect tableId="table-1" />);

  await waitFor(() =>
    expect(navigation.replace).toHaveBeenCalledWith(
      "/dashboard/data-tables/table-1?tab=records&record=row-2",
    ),
  );
  expect(navigation.replace).toHaveBeenCalledTimes(1);
});
