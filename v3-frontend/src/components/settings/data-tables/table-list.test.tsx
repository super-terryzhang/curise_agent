// @vitest-environment jsdom
import React from "react";
import "@/test/setup-dom";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { TableList } from "./table-list";
import * as api from "@/lib/data-tables-api";
import { table } from "@/test/data-tables-fixtures";
vi.mock("@/lib/data-tables-api", async (original) => ({
  ...(await original<typeof api>()),
  listTables: vi.fn(),
  createTable: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({ getUser: () => ({ role: "admin" }) }));
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.listTables).mockResolvedValue({
    items: [],
    page: 1,
    page_size: 50,
    total: 0,
  });
});
it("user creates a named table from the actual form", async () => {
  vi.mocked(api.createTable).mockResolvedValue(table);
  render(<TableList />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "新建表" }));
  await user.type(screen.getByLabelText("表名"), "检验记录");
  await user.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() =>
    expect(api.createTable).toHaveBeenCalledWith(
      expect.objectContaining({ name: "检验记录" }),
    ),
  );
});
it("server field error retains the form rather than silently closing", async () => {
  vi.mocked(api.createTable).mockRejectedValue(new Error("名称不合法"));
  render(<TableList />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "新建表" }));
  await user.type(screen.getByLabelText("表名"), "保留输入");
  await user.click(screen.getByRole("button", { name: "保存" }));
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    expect.stringContaining("名称不合法"),
  );
  expect((screen.getByLabelText("表名") as HTMLInputElement).value).toBe(
    "保留输入",
  );
});
it("a corrected input after definitive rejection is submitted instead of the old request", async () => {
  vi.mocked(api.createTable)
    .mockRejectedValueOnce(new Error("名称不合法"))
    .mockResolvedValueOnce(table);
  render(<TableList />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "新建表" }));
  await user.type(screen.getByLabelText("表名"), "原输入");
  await user.click(screen.getByRole("button", { name: "保存" }));
  await screen.findByRole("alert");
  await user.clear(screen.getByLabelText("表名"));
  await user.type(screen.getByLabelText("表名"), "修正输入");
  await user.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() =>
    expect(api.createTable).toHaveBeenLastCalledWith(
      expect.objectContaining({ name: "修正输入" }),
    ),
  );
});
it("disabled module response cannot leave a usable create action", async () => {
  vi.mocked(api.listTables).mockRejectedValue(
    new api.DataTablesApiError(503, "MODULE_DISABLED", "自定义数据表暂未启用"),
  );
  render(<TableList />);
  await screen.findByRole("alert");
  expect(screen.queryByRole("button", { name: "新建表" })).toBeNull();
});
it("an uncertain creation cannot be replaced by a second new table form", async () => {
  vi.mocked(api.createTable).mockRejectedValue(
    new api.DataTablesApiError(
      0,
      "NETWORK_ERROR",
      "保存结果无法确认",
      [],
      true,
    ),
  );
  render(<TableList />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "新建表" }));
  await user.type(screen.getByLabelText("表名"), "原请求");
  await user.click(screen.getByRole("button", { name: "保存" }));
  await screen.findByRole("alert");
  expect(
    (screen.getByRole("button", { name: "新建表" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await user.click(screen.getByRole("button", { name: "新建表" }));
  expect((screen.getByLabelText("表名") as HTMLInputElement).value).toBe(
    "原请求",
  );
  expect(
    screen.getByRole("button", { name: "使用原请求核对／重试" }),
  ).toBeTruthy();
});
it("refreshing the list cannot unlock or replace an uncertain original request", async () => {
  vi.mocked(api.createTable).mockRejectedValue(
    new api.DataTablesApiError(
      0,
      "NETWORK_ERROR",
      "保存结果无法确认",
      [],
      true,
    ),
  );
  render(<TableList />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "新建表" }));
  await user.type(screen.getByLabelText("表名"), "原请求");
  await user.click(screen.getByRole("button", { name: "保存" }));
  await screen.findByRole("alert");
  await user.click(screen.getByRole("button", { name: "刷新" }));
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "刷新" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false),
  );
  expect((screen.getByLabelText("表名") as HTMLInputElement).disabled).toBe(
    true,
  );
  expect(
    screen.getByRole("button", { name: "使用原请求核对／重试" }),
  ).toBeTruthy();
});
