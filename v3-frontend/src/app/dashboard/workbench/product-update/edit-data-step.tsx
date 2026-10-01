"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type {
  CategoryItem,
  CountryItem,
  PortItem,
  SupplierItem,
} from "@/lib/data-api";
import {
  applyUniformValues,
  BASIC_EDIT_FIELDS,
  changedRows,
  createEditRows,
  rowIssues,
  scopeLabel,
  type EditOperation,
  type EditRow,
  type EditScope,
  type EditValues,
} from "@/lib/product-batch-edit";

export interface EditMasters {
  categories: CategoryItem[];
  suppliers: SupplierItem[];
  countries: CountryItem[];
  ports: PortItem[];
}
interface Props {
  rows: EditRow[];
  scope: EditScope;
  operation: EditOperation;
  masters: EditMasters;
  busy: boolean;
  onRowsChange: (rows: EditRow[]) => void;
  onBack: () => void;
  onCheck: () => void;
}
const selectClass =
  "h-9 min-w-36 rounded-md border border-input bg-background px-2 text-sm";
function ValueInput({
  field,
  label,
  value,
  country,
  masters,
  onChange,
}: {
  field: string;
  label: string;
  value: EditValues[string];
  country?: EditValues[string];
  masters: EditMasters;
  onChange: (value: EditValues[string]) => void;
}) {
  const options =
    field === "supplier_id"
      ? masters.suppliers
      : field === "category_id"
        ? masters.categories
        : field === "country_id"
          ? masters.countries
          : field === "port_id"
            ? masters.ports.filter(
                (p) => !country || p.country_id === Number(country),
              )
            : null;
  return options ? (
    <select
      aria-label={label}
      className={selectClass}
      value={value ?? ""}
      onChange={(e) =>
        onChange(
          e.target.value && e.target.value !== "__clear__"
            ? Number(e.target.value)
            : null,
        )
      }
    >
      <option value="">请选择</option>
      {["supplier_id", "category_id"].includes(field) && (
        <option value="__clear__">清空（不设置）</option>
      )}
      {options.map((item) => (
        <option key={item.id} value={item.id}>
          {item.name}
        </option>
      ))}
    </select>
  ) : (
    <Input
      aria-label={label}
      className={field === "product_name_en" ? "min-w-60" : "min-w-32"}
      value={value ?? ""}
      type={field.startsWith("effective_") ? "date" : "text"}
      inputMode={field === "amount" ? "decimal" : undefined}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function EditDataStep({
  rows,
  scope,
  operation,
  masters,
  busy,
  onRowsChange,
  onBack,
  onCheck,
}: Props) {
  const [page, setPage] = useState(0);
  const [basicField, setBasicField] = useState("supplier_id");
  const [basicValue, setBasicValue] = useState<EditValues[string]>(null);
  const [basicTouched, setBasicTouched] = useState(false);
  const [dates, setDates] = useState({ effective_from: "", effective_to: "" });
  const [enabledDates, setEnabledDates] = useState({
    effective_from: false,
    effective_to: false,
  });
  const fields =
    scope === "basic"
      ? BASIC_EDIT_FIELDS
      : [
          ["amount", scope === "purchase" ? "采购价" : "卖价"],
          ["currency", "币种"],
          ["effective_from", "开始日期"],
          ["effective_to", "结束日期"],
        ];
  const pageCount = Math.max(1, Math.ceil(rows.length / 20));
  const currentPage = Math.min(page, pageCount - 1);
  const visible = rows.slice(currentPage * 20, currentPage * 20 + 20);
  const pending = changedRows(rows, operation);
  const issues = pending.flatMap((row) => rowIssues(row, scope));
  const datePatch = Object.fromEntries(
    Object.entries(dates).filter(
      ([key]) => enabledDates[key as keyof typeof enabledDates],
    ),
  );
  const changeRow = (key: string, field: string, value: EditValues[string]) =>
    onRowsChange(
      rows.map((row) =>
        row.key === key
          ? { ...row, values: { ...row.values, [field]: value } }
          : row,
      ),
    );
  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-base font-semibold">
          {operation === "add" ? "新增" : "修改"}
          {scopeLabel(scope)}
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          共 {rows.length}{" "}
          行。可统一填写，也可逐行修改；没有修改的字段保持原值。
        </p>
      </div>
      <div className="space-y-3 rounded-md border bg-muted/20 p-4">
        <h3 className="text-sm font-medium">统一填写</h3>
        <div className="flex flex-wrap items-end gap-3">
          {scope === "basic" ? (
            <>
              <label className="space-y-1 text-sm">
                字段
                <select
                  aria-label="统一修改字段"
                  className={`${selectClass} block`}
                  value={basicField}
                  onChange={(e) => {
                    setBasicField(e.target.value);
                    setBasicValue(null);
                    setBasicTouched(false);
                  }}
                >
                  {BASIC_EDIT_FIELDS.map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <ValueInput
                field={basicField}
                label="统一填写值"
                value={basicValue}
                masters={masters}
                onChange={(value) => {
                  setBasicValue(value);
                  setBasicTouched(true);
                }}
              />
            </>
          ) : (
            (["effective_from", "effective_to"] as const).map((field) => (
              <label key={field} className="space-y-1 text-sm">
                <span className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={enabledDates[field]}
                    onChange={(e) =>
                      setEnabledDates({
                        ...enabledDates,
                        [field]: e.target.checked,
                      })
                    }
                  />
                  {field === "effective_from" ? "开始日期" : "结束日期"}
                </span>
                <Input
                  aria-label={`统一${field === "effective_from" ? "开始日期" : "结束日期"}`}
                  type="date"
                  disabled={!enabledDates[field]}
                  value={dates[field]}
                  onChange={(e) =>
                    setDates({ ...dates, [field]: e.target.value })
                  }
                />
              </label>
            ))
          )}
          <Button
            variant="outline"
            disabled={
              busy ||
              (scope === "basic"
                ? !basicTouched
                : Object.keys(datePatch).length === 0)
            }
            onClick={() =>
              onRowsChange(
                applyUniformValues(
                  rows,
                  scope === "basic" ? { [basicField]: basicValue } : datePatch,
                ),
              )
            }
          >
            应用到全部 {rows.length} 行
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          {scope === "basic"
            ? "应用后仍可在表格中调整单个产品。"
            : "只更新已勾选的日期；各产品的价格、币种及其他日期不变。"}
        </p>
      </div>
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>行 / 产品</TableHead>
              {fields.map(([key, label]) => (
                <TableHead key={key}>{label}</TableHead>
              ))}
              <TableHead>检查 / 操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {visible.map((row, i) => (
              <TableRow key={row.key}>
                <TableCell className="min-w-56 align-top">
                  <div className="font-mono text-xs">
                    {currentPage * 20 + i + 1} · {row.product.code || "—"}
                  </div>
                  <div className="mt-1 max-w-64 whitespace-normal">
                    {row.product.product_name_en}
                  </div>
                  {scope !== "basic" && (
                    <details className="mt-2 text-xs text-muted-foreground">
                      <summary className="cursor-pointer">
                        查看已有{scope === "purchase" ? "采购价" : "卖价"}区间
                      </summary>
                      <div className="mt-1 max-h-36 overflow-auto">
                        {(row.product.price_periods ?? [])
                          .filter((p) => p.status && p.price_type === scope)
                          .map((p) => (
                            <div key={p.id}>
                              {p.effective_from.slice(0, 10)} ~{" "}
                              {p.effective_to.slice(0, 10)}：{p.amount}{" "}
                              {p.currency}
                            </div>
                          ))}
                      </div>
                    </details>
                  )}
                </TableCell>
                {fields.map(([key, label]) => (
                  <TableCell
                    key={key}
                    className={
                      row.values[key] !== row.original[key]
                        ? "bg-amber-50/70 align-top"
                        : "align-top"
                    }
                  >
                    <ValueInput
                      field={key}
                      label={`第 ${currentPage * 20 + i + 1} 行${label}`}
                      value={row.values[key]}
                      country={row.values.country_id}
                      masters={masters}
                      onChange={(value) => changeRow(row.key, key, value)}
                    />
                    {operation === "edit" &&
                      row.values[key] !== row.original[key] && (
                        <div className="mt-1 text-xs text-muted-foreground">
                          原值：{String(row.original[key] ?? "—") || "—"}
                        </div>
                      )}
                  </TableCell>
                ))}
                <TableCell className="min-w-52 align-top">
                  <div className="text-xs text-destructive">
                    {rowIssues(row, scope).map((issue) => (
                      <p key={issue}>{issue}</p>
                    ))}
                  </div>
                  {operation === "add" && (
                    <div className="mt-2 space-y-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          const next = createEditRows(
                            [row.product],
                            scope,
                            "add",
                          )[0];
                          const key = `${row.product.id}:new:${crypto.randomUUID()}`;
                          onRowsChange([...rows, { ...next, key }]);
                          setPage(Math.floor(rows.length / 20));
                        }}
                      >
                        再添加一个区间
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() =>
                          onRowsChange(
                            rows.filter((item) => item.key !== row.key),
                          )
                        }
                      >
                        移除此行
                      </Button>
                    </div>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <div className="flex items-center justify-end gap-3 border-t p-3 text-xs">
          <Button
            variant="outline"
            size="sm"
            disabled={currentPage === 0}
            onClick={() => setPage(currentPage - 1)}
          >
            上一页
          </Button>
          {currentPage + 1} / {pageCount}
          <Button
            variant="outline"
            size="sm"
            disabled={currentPage + 1 >= pageCount}
            onClick={() => setPage(currentPage + 1)}
          >
            下一页
          </Button>
        </div>
      </div>
      <div className="flex items-center justify-between border-t pt-4">
        <Button variant="outline" disabled={busy} onClick={onBack}>
          上一步
        </Button>
        <span className="text-sm text-muted-foreground">
          {pending.length} 行待更新
          {issues.length ? ` · ${issues.length} 项待修正` : ""}
        </span>
        <Button
          disabled={busy || !pending.length || issues.length > 0}
          onClick={onCheck}
        >
          {busy ? "正在检查…" : "检查并核对"}
        </Button>
      </div>
    </section>
  );
}
