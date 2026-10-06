// @vitest-environment jsdom
import React from "react";
import "@/test/setup-dom";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { LinkedRecordPicker } from "./linked-record-picker";
import * as api from "@/lib/data-tables-api";
import { record } from "@/test/data-tables-fixtures";
vi.mock("@/lib/data-tables-api");
it("same display labels are selectable by distinct record IDs", async () => {
  vi.mocked(api.searchLinkTargets).mockResolvedValue({
    items: [
      { ...record, id: "a", display_label: "同名" },
      { ...record, id: "b", display_label: "同名" },
    ],
    total: 2,
    page: 1,
    page_size: 50,
  });
  const onChange = vi.fn();
  render(
    <LinkedRecordPicker
      tableId="t"
      fieldId="f"
      value={null}
      onChange={onChange}
    />,
  );
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "选择 同名 · a" }),
  );
  await user.click(screen.getByRole("button", { name: "选择 同名 · b" }));
  expect(onChange.mock.calls).toEqual([["a"], ["b"]]);
});
it("a late old search cannot replace the current results", async () => {
  let finish: (
    v: Awaited<ReturnType<typeof api.searchLinkTargets>>,
  ) => void = () => {};
  vi.mocked(api.searchLinkTargets).mockImplementation((_t, _f, q) =>
    q?.q === "旧"
      ? new Promise((resolve) => {
          finish = resolve;
        })
      : Promise.resolve({
          items:
            q?.q === "新" ? [{ ...record, display_label: "新查询结果" }] : [],
          total: 1,
          page: 1,
          page_size: 50,
        }),
  );
  render(
    <LinkedRecordPicker
      tableId="t"
      fieldId="f"
      value={null}
      onChange={vi.fn()}
    />,
  );
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("搜索关联记录"), "旧");
  await user.clear(screen.getByLabelText("搜索关联记录"));
  await user.type(screen.getByLabelText("搜索关联记录"), "新");
  await screen.findByRole("button", { name: /选择 新查询结果/ });
  await act(async () =>
    finish({
      items: [{ ...record, display_label: "旧查询结果" }],
      total: 1,
      page: 1,
      page_size: 50,
    }),
  );
  expect(screen.queryByText(/旧查询结果/)).toBeNull();
});
