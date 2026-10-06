"use client";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { DataField, Value, LinkedLabel } from "@/lib/data-tables-types";
import {
  fieldControlKind,
  displayValue,
  fromDateTimeInput,
  toDateTimeInput,
} from "@/lib/data-tables-view";
import { LinkedRecordPicker } from "./linked-record-picker";
import { SELECT_CLASS } from "./shared";

export function ValueControl({
  field,
  value,
  onChange,
  disabled = false,
  label = field.label,
  initialLabel,
}: {
  field: DataField;
  value: Value | undefined;
  onChange: (v: Value) => void;
  disabled?: boolean;
  label?: string;
  initialLabel?: LinkedLabel;
}) {
  const kind = fieldControlKind(field),
    id = `value-${field.id}`;
  if (kind === "unsupported")
    return (
      <p className="text-sm text-destructive">
        不支持的字段类型，请联系管理员；原值保留，不可编辑。
      </p>
    );
  if (kind === "link")
    return (
      <LinkedRecordPicker
        tableId={field.table_id}
        fieldId={field.id}
        value={typeof value === "string" ? value : null}
        onChange={onChange}
        disabled={disabled}
        initialLabel={initialLabel}
      />
    );
  if (disabled)
    return (
      <p className="text-sm whitespace-pre-wrap break-words">
        {displayValue(field, value)}
      </p>
    );
  if (kind === "boolean")
    return (
      <select
        id={id}
        aria-label={label}
        className={SELECT_CLASS + " w-full"}
        value={value === null || value === undefined ? "" : String(value)}
        onChange={(e) =>
          onChange(e.target.value === "" ? null : e.target.value === "true")
        }
      >
        <option value="">未填写</option>
        <option value="true">是</option>
        <option value="false">否</option>
      </select>
    );
  if (kind === "single_select")
    return (
      <select
        id={id}
        aria-label={label}
        className={SELECT_CLASS + " w-full"}
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value || null)}
      >
        <option value="">未填写</option>
        {field.config.options
          ?.filter((o) => o.active || o.id === value)
          .map((o) => (
            <option key={o.id} value={o.id} disabled={!o.active}>
              {o.label}
              {!o.active ? "（已停用，原值保留）" : ""}
            </option>
          ))}
      </select>
    );
  if (kind === "multi_select")
    return (
      <div className="flex flex-wrap gap-3">
        {field.config.options
          ?.filter(
            (o) => o.active || (Array.isArray(value) && value.includes(o.id)),
          )
          .map((o) => (
            <label className="text-sm" key={o.id}>
              <input
                aria-label={`${label} · ${o.label}`}
                type="checkbox"
                checked={Array.isArray(value) && value.includes(o.id)}
                onChange={(e) => {
                  const existing = Array.isArray(value) ? value : [];
                  onChange(
                    e.target.checked
                      ? [...existing, o.id]
                      : existing.filter((v) => v !== o.id),
                  );
                }}
              />{" "}
              {o.label}
              {!o.active ? "（停用，可移除，不能重新选择）" : ""}
            </label>
          ))}
      </div>
    );
  const raw =
    kind === "datetime"
      ? toDateTimeInput(String(value ?? ""))
      : String(value ?? "");
  const change = (v: string) =>
    onChange(kind === "datetime" ? fromDateTimeInput(v) : v === "" ? null : v);
  return kind === "text" && field.config.multiline ? (
    <Textarea
      id={id}
      aria-label={label}
      value={raw}
      onChange={(e) => change(e.target.value)}
    />
  ) : (
    <Input
      id={id}
      aria-label={label}
      type={
        kind === "date"
          ? "date"
          : kind === "datetime"
            ? "datetime-local"
            : "text"
      }
      step={kind === "datetime" ? 1 : undefined}
      inputMode={kind === "number" ? "decimal" : undefined}
      value={raw}
      onChange={(e) => change(e.target.value)}
    />
  );
}
