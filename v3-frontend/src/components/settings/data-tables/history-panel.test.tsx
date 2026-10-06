// @vitest-environment jsdom
import React from "react";
import "@/test/setup-dom";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { HistoryPanel } from "./history-panel";
import * as api from "@/lib/data-tables-api";
import { field } from "@/test/data-tables-fixtures";
vi.mock("@/lib/data-tables-api");
it("history uses the saved field labels and old/new values, always read-only", async () => {
  vi.mocked(api.listChanges).mockResolvedValue({
    items: [
      {
        id: "h",
        table_id: "t",
        entity_type: "record",
        entity_id: "r",
        record_id: "r",
        field_id: null,
        action: "update_record",
        before: { values: { f: "旧值" } },
        after: { values: { f: "新值" } },
        display_snapshot: { fields: { f: { ...field(), label: "旧字段名" } } },
        actor_id: 1,
        actor_role: "admin",
        schema_version: 2,
        revision: 2,
        created_at: "2026-10-06T00:00:00Z",
      },
    ],
    total: 1,
    page: 1,
    page_size: 50,
  });
  render(<HistoryPanel tableId="t" />);
  await userEvent
    .setup()
    .click(await screen.findByRole("button", { name: "查看变更" }));
  expect(screen.getByRole("cell", { name: "旧字段名" })).toBeTruthy();
  expect(screen.getByRole("cell", { name: "旧值" })).toBeTruthy();
  expect(screen.getByRole("cell", { name: "新值" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "保存" })).toBeNull();
});
