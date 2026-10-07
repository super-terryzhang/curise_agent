// @vitest-environment jsdom
import React from "react";
import "@/test/setup-dom";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import * as api from "@/lib/data-tables-api";
import { TableDetail } from "./table-detail";
import {
  coreField,
  field,
  record,
  systemTable,
} from "@/test/data-tables-fixtures";

vi.mock("@/lib/data-tables-api", async (original) => ({
  ...(await original<typeof api>()),
  getTable: vi.fn(),
  listFields: vi.fn(),
  listRecords: vi.fn(),
  getRecord: vi.fn(),
  saveSystemRecord: vi.fn(),
  searchLinkTargets: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({ getUser: () => ({ role: "admin" }) }));

const extension = {
  ...field("extension"),
  table_id: systemTable.id,
  label: "内部备注",
};
const systemRecord = {
  ...record,
  id: "7",
  table_id: systemTable.id,
  revision: 0,
  values: { [coreField.id]: "P-007", extension: null },
  display_label: "验收产品",
  business_url: "/dashboard/data/products/7",
  created_by: null,
  updated_by: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getTable).mockResolvedValue(systemTable);
  vi.mocked(api.listFields).mockResolvedValue([coreField, extension]);
  vi.mocked(api.listRecords).mockResolvedValue({
    items: [systemRecord],
    total: 1,
    page: 1,
    page_size: 50,
  });
  vi.mocked(api.getRecord).mockResolvedValue(systemRecord);
  vi.mocked(api.saveSystemRecord).mockResolvedValue({
    ...systemRecord,
    revision: 1,
    values: { ...systemRecord.values, extension: "已核对" },
  });
});

it("opens a core record inside the unified table and saves only its extension", async () => {
  render(
    <TableDetail
      tableId={systemTable.id}
      activeTab="records"
      recordId="7"
    />,
  );
  expect(await screen.findByRole("heading", { name: "产品" })).toBeTruthy();
  expect(await screen.findByText("产品代码（只读）")).toBeTruthy();
  expect(screen.getAllByText("P-007")).toHaveLength(2);
  expect(
    screen
      .getAllByRole("link", { name: "打开业务页面" })
      .every((link) => link.getAttribute("href") === "/dashboard/data/products/7"),
  ).toBe(true);

  const user = userEvent.setup();
  await user.type(screen.getByLabelText("内部备注"), "已核对");
  await user.click(screen.getByRole("button", { name: "保存扩展信息" }));

  await waitFor(() => expect(api.saveSystemRecord).toHaveBeenCalledOnce());
  expect(api.saveSystemRecord).toHaveBeenCalledWith(
    systemTable.id,
    "7",
    expect.objectContaining({
      source_record_id: "7",
      expected_revision: 0,
      values: { extension: "已核对" },
    }),
  );
});
