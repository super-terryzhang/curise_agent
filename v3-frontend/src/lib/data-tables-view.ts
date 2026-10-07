import type { DataField, Values, Value, Issue } from "./data-tables-types";
export const TYPE_LABELS = {
  text: "文本",
  number: "数字",
  date: "日期",
  datetime: "日期时间",
  single_select: "单选",
  multi_select: "多选",
  boolean: "是／否",
  link: "关联记录",
};
export function fieldControlKind(f: DataField) {
  return Object.hasOwn(TYPE_LABELS, f.field_type)
    ? f.field_type
    : "unsupported";
}
export function displayValue(f: DataField, value: Value | undefined): string {
  if (value === null || value === undefined) return "—";
  if (f.field_type === "boolean") return value ? "是" : "否";
  if (f.field_type === "datetime") return formatTime(String(value));
  if (f.field_type === "single_select" || f.field_type === "multi_select")
    return (
      (Array.isArray(value) ? value : [String(value)])
        .map(
          (id) =>
            f.config.options?.find((o) => o.id === id)?.label ||
            `未知选项 ${id}`,
        )
        .join("、") || "—"
    );
  return Array.isArray(value) ? value.join("、") : String(value);
}
export function buildRecordValues(
  fields: DataField[],
  draft: Values,
  original?: Values,
): Values {
  const values: Values = {};
  for (const f of fields)
    if (
      f.status === "active" &&
      Object.hasOwn(draft, f.id) &&
      (!original ||
        JSON.stringify(draft[f.id]) !== JSON.stringify(original[f.id]))
    )
      values[f.id] = draft[f.id];
  return values;
}
export function fieldIssues(error: { issues?: Issue[] }) {
  const result: Record<string, string[]> = {};
  for (const i of error.issues || [])
    if (i.field_id) (result[i.field_id] ||= []).push(i.message);
  return result;
}
export const canManageStructure = (role?: string) =>
  role === "admin" || role === "superadmin";
export const canManageRecords = (role?: string) =>
  ["admin", "superadmin", "employee", "finance"].includes(role || "");
export const parseTableTab = (value: string | null) =>
  value === "records" || value === "history" ? value : "fields";
export function formatTime(v: string) {
  return new Date(v).toLocaleString("zh-CN", {
    timeZone: "Asia/Tokyo",
    hour12: false,
  });
}
export function toDateTimeInput(v: string) {
  if (!v) return "";
  const d = new Date(v);
  if (!Number.isFinite(d.getTime())) return "";
  return new Date(d.getTime() + 9 * 3600000).toISOString().slice(0, 19);
}
export function fromDateTimeInput(v: string): string | null {
  if (!v) return null;
  const d = new Date(v + "+09:00");
  return Number.isFinite(d.getTime()) ? d.toISOString() : v;
}
