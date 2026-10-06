export type FieldType =
  | "text"
  | "number"
  | "date"
  | "datetime"
  | "single_select"
  | "multi_select"
  | "boolean"
  | "link";
export type Status = "active" | "archived";
export type Value = string | boolean | string[] | null;
export type Values = Record<string, Value>;
export interface SelectOption {
  id: string;
  label: string;
  active: boolean;
}
export interface FieldConfig {
  max_length?: number;
  multiline?: boolean;
  precision?: number;
  scale?: number;
  options?: SelectOption[];
}
export interface FieldDefinition {
  id: string;
  label: string;
  field_type: FieldType;
  required: boolean;
  unique: boolean;
  default_value: Value;
  config: FieldConfig;
  target_table_id: string | null;
}
export interface DataField extends FieldDefinition {
  table_id: string;
  source: "core" | "extension";
  locked: boolean;
  system_key: string | null;
  status: Status;
  sort_order: number;
  schema_version: number;
  created_at: string;
  updated_at: string;
}
export interface TableCreate {
  id: string;
  name: string;
  description?: string | null;
}
export interface DataTable extends TableCreate {
  table_kind: "user" | "system";
  system_key: "products" | "suppliers" | "orders" | null;
  status: Status;
  schema_version: number;
  display_field_id: string | null;
  created_at: string;
  updated_at: string;
  created_by: number | null;
  updated_by: number | null;
  field_count: number;
  record_count: number;
}
export interface SchemaAction {
  expected_schema_version: number;
}
export interface TableUpdate extends SchemaAction {
  name?: string;
  description?: string | null;
  display_field_id?: string | null;
}
export interface FieldCreate extends FieldDefinition, SchemaAction {}
export type FieldUpdate = Partial<Omit<FieldDefinition, "id">> & SchemaAction;
export interface RecordCreate {
  id: string;
  schema_version: number;
  values: Values;
}
export interface RecordAction {
  expected_revision: number;
  schema_version: number;
}
export interface RecordUpdate extends RecordAction {
  values: Values;
}
export interface LinkedLabel {
  record_id: string;
  table_id: string;
  table_name: string;
  display_label: string;
  status: string;
}
export interface DataRecord extends RecordCreate {
  table_id: string;
  revision: number;
  status: Status;
  created_at: string;
  updated_at: string;
  created_by: number | null;
  updated_by: number | null;
  display_label: string;
  business_url?: string | null;
  linked_labels?: Record<string, LinkedLabel>;
}
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}
export interface Issue {
  table_id?: string | null;
  record_id?: string | null;
  field_id?: string | null;
  field_label?: string | null;
  code: string;
  message: string;
}
export interface RecordFilter {
  field_id: string;
  operator: "eq" | "contains" | "lt" | "lte" | "gt" | "gte" | "is_empty";
  value?: Value;
}
export interface RecordQuery {
  status?: Status;
  page?: number;
  page_size?: number;
  sort_field_id?: string;
  sort_direction?: "asc" | "desc";
  filters?: RecordFilter[];
  q?: string;
}
export interface DataChange {
  id: string;
  table_id: string;
  entity_type: string;
  entity_id: string;
  field_id: string | null;
  record_id: string | null;
  action: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  display_snapshot: {
    fields?: Record<string, DataField>;
    links?: Record<
      string,
      {
        record_id: string;
        table_id: string;
        table_name: string;
        display_label: string;
      }
    >;
    target_tables?: Record<string, string>;
  };
  actor_id: number;
  actor_role: string;
  schema_version: number;
  revision: number | null;
  created_at: string;
}
