// @vitest-environment jsdom
import React from "react";
import "@/test/setup-dom";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { FieldsPanel } from "./fields-panel";
import {
  coreField,
  systemTable,
  table,
  field,
} from "@/test/data-tables-fixtures";
import * as api from "@/lib/data-tables-api";
import { getUser } from "@/lib/auth";
vi.mock("@/lib/data-tables-api");
vi.mock("@/lib/auth", () => ({ getUser: vi.fn() }));
it("moves a field and submits complete stable ID order", async () => {
  vi.mocked(getUser).mockReturnValue({ role: "admin" } as ReturnType<
    typeof getUser
  >);
  vi.mocked(api.reorderFields).mockResolvedValue([]);
  render(
    <FieldsPanel
      table={table}
      fields={[field("a"), { ...field("b"), label: "数量" }]}
      onChanged={vi.fn()}
    />,
  );
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "下移 名称" }));
  await waitFor(() =>
    expect(api.reorderFields).toHaveBeenCalledWith(table.id, {
      expected_schema_version: 2,
      field_ids: ["b", "a"],
    }),
  );
});
it("shows locked core fields without edit, reorder or archive controls", () => {
  vi.mocked(getUser).mockReturnValue({ role: "admin" } as ReturnType<
    typeof getUser
  >);
  render(
    <FieldsPanel
      table={systemTable}
      fields={[coreField, { ...field("extension"), table_id: systemTable.id }]}
      onChanged={vi.fn()}
    />,
  );
  const coreRow = screen.getByRole("cell", { name: /产品代码/ }).closest("tr")!;
  expect(coreRow.textContent).toContain("核心字段（锁定）");
  expect(coreRow.textContent).not.toContain("修改");
  expect(coreRow.textContent).not.toContain("归档");
  expect(screen.getByRole("button", { name: "新增字段" })).toBeTruthy();
});
it("writer can read fields but cannot modify structure", () => {
  vi.mocked(getUser).mockReturnValue({ role: "employee" } as ReturnType<
    typeof getUser
  >);
  render(<FieldsPanel table={table} fields={[field()]} onChanged={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "新增字段" })).toBeNull();
  expect(screen.getByRole("cell", { name: "名称" })).toBeTruthy();
});
