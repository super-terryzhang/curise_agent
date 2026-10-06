// @vitest-environment jsdom
import React from "react";
import "@/test/setup-dom";
import { render,screen,waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect,it,vi } from "vitest";
import { FieldEditor } from "./field-editor";
import * as api from "@/lib/data-tables-api";
import { table,field } from "@/test/data-tables-fixtures";
vi.mock("@/lib/data-tables-api");
it("choosing number shows precision and saves string default with schema version",async()=>{
  vi.mocked(api.createField).mockResolvedValue(field());
  render(<FieldEditor table={table} onSaved={vi.fn()} onCancel={vi.fn()}/>);
  const user=userEvent.setup(); await user.type(screen.getByLabelText("字段名称"),"数量");
  await user.selectOptions(screen.getByLabelText("字段类型"),"number");
  await user.clear(screen.getByLabelText("总位数")); await user.type(screen.getByLabelText("总位数"),"10");
  await user.type(screen.getByLabelText("默认值"),"0");
  await user.click(screen.getByRole("button",{name:"保存字段"}));
  await waitFor(()=>expect(api.createField).toHaveBeenCalledWith(table.id,expect.objectContaining({field_type:"number",default_value:"0",expected_schema_version:2,config:{precision:10,scale:6}})));
});
it("existing rows prohibit changing field type in the editor",()=>{
  render(<FieldEditor table={{...table,record_count:1}} field={field()} onSaved={vi.fn()} onCancel={vi.fn()}/>);
  expect((screen.getByLabelText("字段类型") as HTMLSelectElement).disabled).toBe(true);
  expect(screen.getByText(/已有记录.*新建字段/)).toBeTruthy();
});
it("field corrections after a validation rejection are used on next save",async()=>{
  vi.mocked(api.createField).mockRejectedValueOnce(new Error("字段值不合法")).mockResolvedValueOnce(field());
  render(<FieldEditor table={table} onSaved={vi.fn()} onCancel={vi.fn()}/>);
  const user=userEvent.setup();await user.type(screen.getByLabelText("字段名称"),"旧名");
  await user.click(screen.getByRole("button",{name:"保存字段"}));await screen.findByRole("alert");
  await user.clear(screen.getByLabelText("字段名称"));await user.type(screen.getByLabelText("字段名称"),"新名");
  await user.click(screen.getByRole("button",{name:"保存字段"}));
  await waitFor(()=>expect(api.createField).toHaveBeenLastCalledWith(table.id,expect.objectContaining({label:"新名"})));
});
