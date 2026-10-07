import type {
  DataField,
  DataTable,
  DataRecord,
  FieldType,
} from "@/lib/data-tables-types";
export const table: DataTable = {
  id: "00000000-0000-4000-8000-000000000001",
  name: "检验记录",
  description: null,
  table_kind: "user",
  system_key: null,
  status: "active",
  schema_version: 2,
  display_field_id: null,
  field_count: 1,
  record_count: 0,
  created_by: 1,
  updated_by: 1,
  created_at: "2026-10-06T00:00:00Z",
  updated_at: "2026-10-06T00:00:00Z",
};
export function field(id = "f", kind: FieldType = "text"): DataField {
  return {
    id,
    table_id: table.id,
    label: "名称",
    source: "extension",
    locked: false,
    system_key: null,
    field_type: kind,
    required: false,
    unique: false,
    default_value: null,
    config: {},
    target_table_id: null,
    sort_order: 0,
    status: "active",
    schema_version: 2,
    created_at: table.created_at,
    updated_at: table.updated_at,
  };
}
export const systemTable: DataTable = {
  ...table,
  id: "025588dd-ae63-5607-9e78-1179a500ed6e",
  name: "产品",
  table_kind: "system",
  system_key: "products",
  created_by: 0,
  updated_by: 0,
};
export const coreField: DataField = {
  ...field("025588dd-ae63-5607-9e78-1179a500ed70"),
  table_id: systemTable.id,
  label: "产品代码",
  source: "core",
  locked: true,
  system_key: "code",
};
export const record: DataRecord = {
  id: "00000000-0000-4000-8000-000000000003",
  table_id: table.id,
  values: { f: "原值" },
  revision: 1,
  schema_version: 2,
  status: "active",
  display_label: "原值",
  created_by: 1,
  updated_by: 1,
  created_at: table.created_at,
  updated_at: table.updated_at,
};
