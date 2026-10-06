// @vitest-environment jsdom
import React from "react";
import "@/test/setup-dom";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { RecordsPanel } from "./records-panel";
import * as api from "@/lib/data-tables-api";
import { table, field, record } from "@/test/data-tables-fixtures";
vi.mock("@/lib/data-tables-api");
vi.mock("@/lib/auth", () => ({ getUser: () => ({ role: "employee" }) }));
it("pages server data and edits a chosen row", async () => {
  vi.mocked(api.listRecords).mockResolvedValue({
    items: [record],
    total: 51,
    page: 1,
    page_size: 50,
  });
  render(<RecordsPanel table={table} fields={[field()]} onChanged={vi.fn()} />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "下一页" }));
  await waitFor(() =>
    expect(api.listRecords).toHaveBeenLastCalledWith(
      table.id,
      expect.objectContaining({ page: 2 }),
    ),
  );
  await user.click(screen.getByRole("button", { name: "编辑" }));
  expect((screen.getByLabelText("名称") as HTMLInputElement).value).toBe(
    "原值",
  );
});
it("shows relation labels and deep links without recursively fetching every cell", async () => {
  vi.mocked(api.listRecords).mockResolvedValue({
    items: [
      {
        ...record,
        values: { f: "target" },
        linked_labels: {
          f: {
            record_id: "target",
            table_id: "target-table",
            table_name: "客户",
            display_label: "真实客户",
            status: "archived",
          },
        },
      },
    ],
    total: 1,
    page: 1,
    page_size: 50,
  });
  render(
    <RecordsPanel
      table={table}
      fields={[field("f", "link")]}
      onChanged={vi.fn()}
    />,
  );
  const link = await screen.findByRole("link", { name: /真实客户/ });
  expect(link.getAttribute("href")).toBe(
    "/dashboard/settings/data-tables/target-table?tab=records&record=target",
  );
  expect(screen.getByText(/已归档/)).toBeTruthy();
  expect(api.getRecord).not.toHaveBeenCalled();
});
