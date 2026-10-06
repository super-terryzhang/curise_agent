// @vitest-environment jsdom
import React from "react";
import "@/test/setup-dom";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { RecordEditor } from "./record-editor";
import * as api from "@/lib/data-tables-api";
import { table, field, record } from "@/test/data-tables-fixtures";
vi.mock("@/lib/data-tables-api", async (original) => ({
  ...(await original<typeof api>()),
  createRecord: vi.fn(),
  updateRecord: vi.fn(),
  searchLinkTargets: vi.fn(),
  getRecord: vi.fn(),
  getTable: vi.fn(),
  listFields: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({ getUser: () => ({ role: "admin" }) }));
beforeEach(() => vi.clearAllMocks());
it("explicit reload after a schema conflict allows editing even when parent has not refreshed", async () => {
  vi.mocked(api.updateRecord).mockRejectedValue(
    new api.DataTablesApiError(409, "STALE_SCHEMA", "配置已更新"),
  );
  vi.mocked(api.getTable).mockResolvedValue({ ...table, schema_version: 3 });
  vi.mocked(api.listFields).mockResolvedValue([field()]);
  vi.mocked(api.getRecord).mockResolvedValue({
    ...record,
    schema_version: 3,
    revision: 2,
  });
  render(
    <RecordEditor
      table={table}
      fields={[field()]}
      record={record}
      onSaved={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("名称"), "草稿");
  await user.click(screen.getByRole("button", { name: "保存记录" }));
  await user.click(
    await screen.findByRole("button", { name: "载入最新并重新编辑" }),
  );
  await user.click(screen.getByRole("button", { name: "确认放弃并刷新" }));
  await waitFor(() =>
    expect((screen.getByLabelText("名称") as HTMLInputElement).value).toBe(
      "原值",
    ),
  );
  expect(
    (screen.getByRole("button", { name: "保存记录" }) as HTMLButtonElement)
      .disabled,
  ).toBe(false);
});
it("eight dynamic controls save UUID options, decimal zero and boolean false", async () => {
  const kinds = [
    "text",
    "number",
    "date",
    "datetime",
    "single_select",
    "multi_select",
    "boolean",
    "link",
  ] as const;
  const fields = kinds.map((k, i) => ({
    ...field("f" + i, k),
    label: k,
    config: k.includes("select")
      ? { options: [{ id: "o", label: "已选", active: true }] }
      : {},
    target_table_id: k === "link" ? "target" : null,
  }));
  vi.mocked(api.searchLinkTargets).mockResolvedValue({
    items: [{ ...record, id: "target-record", display_label: "目标名称" }],
    total: 1,
    page: 1,
    page_size: 50,
  });
  vi.mocked(api.createRecord).mockResolvedValue(record);
  render(
    <RecordEditor
      table={table}
      fields={fields}
      onSaved={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("text"), "测试");
  await user.type(screen.getByLabelText("number"), "0");
  await user.type(screen.getByLabelText("date"), "2026-10-06");
  await user.type(screen.getByLabelText("datetime"), "2026-10-06T09:00:00");
  await user.selectOptions(screen.getByLabelText("single_select"), "o");
  await user.click(screen.getByLabelText("multi_select · 已选"));
  await user.selectOptions(screen.getByLabelText("boolean"), "false");
  await user.click(
    await screen.findByRole("button", { name: /选择 目标名称/ }),
  );
  await user.click(screen.getByRole("button", { name: "保存记录" }));
  await waitFor(() =>
    expect(api.createRecord).toHaveBeenCalledWith(
      table.id,
      expect.objectContaining({
        values: {
          f0: "测试",
          f1: "0",
          f2: "2026-10-06",
          f3: "2026-10-06T00:00:00.000Z",
          f4: "o",
          f5: ["o"],
          f6: false,
          f7: "target-record",
        },
      }),
    ),
  );
});
it("Chinese field error retains all inputs and cancellation never saves", async () => {
  vi.mocked(api.createRecord).mockRejectedValue(
    new api.DataTablesApiError(422, "REQUIRED", "请检查字段", [
      { field_id: "f", code: "REQUIRED", message: "名称不能为空" },
    ]),
  );
  const cancel = vi.fn();
  render(
    <RecordEditor
      table={table}
      fields={[field()]}
      onSaved={vi.fn()}
      onCancel={cancel}
    />,
  );
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("名称"), "保留");
  await user.click(screen.getByRole("button", { name: "保存记录" }));
  await screen.findAllByText("名称不能为空");
  expect((screen.getByLabelText("名称") as HTMLInputElement).value).toBe(
    "保留",
  );
  await user.click(screen.getByRole("button", { name: "取消" }));
  await user.click(screen.getByRole("button", { name: "放弃未保存修改" }));
  expect(cancel).toHaveBeenCalledOnce();
  expect(api.createRecord).toHaveBeenCalledOnce();
});
it("409 leaves the draft untouched and requires explicit refresh before another save", async () => {
  vi.mocked(api.updateRecord).mockRejectedValue(
    new api.DataTablesApiError(409, "STALE_REVISION", "其他人已修改"),
  );
  render(
    <RecordEditor
      table={table}
      fields={[field()]}
      record={record}
      onSaved={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("名称"), "草稿");
  await user.click(screen.getByRole("button", { name: "保存记录" }));
  await screen.findByText("其他人已修改");
  expect((screen.getByLabelText("名称") as HTMLInputElement).value).toBe(
    "原值草稿",
  );
  expect(
    (screen.getByRole("button", { name: "保存记录" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});
it("uncertain create retry retains original UUID and original body", async () => {
  vi.mocked(api.createRecord)
    .mockRejectedValueOnce(
      new api.DataTablesApiError(
        0,
        "NETWORK_ERROR",
        "保存结果无法确认",
        [],
        true,
      ),
    )
    .mockResolvedValueOnce(record);
  render(
    <RecordEditor
      table={table}
      fields={[field()]}
      onSaved={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("名称"), "原请求");
  await user.click(screen.getByRole("button", { name: "保存记录" }));
  await user.click(
    await screen.findByRole("button", { name: "使用原请求核对／重试" }),
  );
  const calls = vi.mocked(api.createRecord).mock.calls;
  expect(calls).toHaveLength(2);
  expect(calls[1][1]).toEqual(calls[0][1]);
});
it("failed result lookup cannot unlock an uncertain creation draft", async () => {
  vi.mocked(api.createRecord).mockRejectedValue(
    new api.DataTablesApiError(
      0,
      "NETWORK_ERROR",
      "保存结果无法确认",
      [],
      true,
    ),
  );
  vi.mocked(api.getRecord).mockRejectedValue(
    new api.DataTablesApiError(0, "NETWORK_ERROR", "读取失败"),
  );
  render(
    <RecordEditor
      table={table}
      fields={[field()]}
      onSaved={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("名称"), "原内容");
  await user.click(screen.getByRole("button", { name: "保存记录" }));
  await user.click(
    await screen.findByRole("button", { name: "核对已保存记录" }),
  );
  expect(
    await screen.findByRole("button", { name: "使用原请求核对／重试" }),
  ).toBeTruthy();
  expect(screen.queryByRole("textbox", { name: "名称" })).toBeNull();
});
it("previously selected inactive option can be removed but never reselected", async () => {
  const f = {
    ...field("f", "multi_select"),
    config: { options: [{ id: "old", label: "旧选项", active: false }] },
  };
  vi.mocked(api.updateRecord).mockResolvedValue(record);
  render(
    <RecordEditor
      table={table}
      fields={[f]}
      record={{ ...record, values: { f: ["old"] } }}
      onSaved={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  const user = userEvent.setup();
  await user.click(screen.getByLabelText("名称 · 旧选项"));
  expect(screen.queryByLabelText("名称 · 旧选项")).toBeNull();
  await user.click(screen.getByRole("button", { name: "保存记录" }));
  expect(api.updateRecord).toHaveBeenLastCalledWith(
    table.id,
    record.id,
    expect.objectContaining({ values: { f: [] } }),
  );
});
