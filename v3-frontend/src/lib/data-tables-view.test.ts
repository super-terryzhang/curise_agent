import { expect, it } from "vitest";
import { buildRecordValues, displayValue, fieldControlKind, toDateTimeInput, fromDateTimeInput, parseTableTab, canManageStructure, canManageRecords, fieldIssues } from "./data-tables-view";
import type { DataField } from "./data-tables-types";
const f = (id:string, field_type:DataField["field_type"], status="active") => ({id,field_type,status,config:{}} as DataField);
it("preserves false, decimal strings, null and PATCH absence, omits archived fields", () => {
  const fields = [f("a","boolean"), f("b","number"), f("c","text"),f("d","text","archived")];
  expect(buildRecordValues(fields, {a:false,b:"0",c:null,d:"changed"}, {a:true,b:"1",c:"old",d:"old"})).toEqual({a:false,b:"0",c:null});
  expect(buildRecordValues(fields, {b:"0"}, {b:"0"})).toEqual({});
  expect(displayValue(fields[0],false)).toBe("否");
  expect(displayValue(fields[1],"123456789012.123456")).toBe("123456789012.123456");
});
it("uses Japan datetime without shifting calendar date fields", () => {
  expect(toDateTimeInput("2026-10-05T18:30:00Z")).toBe("2026-10-06T03:30:00");
  expect(fromDateTimeInput("2026-10-06T03:30:00")).toBe("2026-10-05T18:30:00.000Z");
  expect(displayValue(f("d","date"),"2026-10-06")).toBe("2026-10-06");
});
it("unknown controls never become silently editable; roles and errors remain explicit", () => {
  expect(fieldControlKind({field_type:"future"} as unknown as DataField)).toBe("unsupported");
  expect(canManageStructure("employee")).toBe(false);
  expect(canManageRecords("finance")).toBe(true);
  expect(canManageRecords("customer")).toBe(false);
  expect(parseTableTab("bad")).toBe("fields");
  expect(fieldIssues({issues:[{field_id:"a",code:"INVALID",message:"错误"},{code:"INVALID",message:"整体错误"}]})).toEqual({a:["错误"]});
});
