import type { ProductItem, ProductPricePeriod } from "./data-api";

export type EditScope = "basic" | "purchase" | "selling";
export type EditOperation = "edit" | "add";
export type EditValues = Record<string, string | number | null>;

export interface EditRow {
  key: string;
  product: ProductItem;
  periodId: number | null;
  values: EditValues;
  original: EditValues;
}
export interface PeriodTarget {
  product: ProductItem;
  period: ProductPricePeriod;
}
export interface DirectUpdateRequest {
  selected_product_ids: number[];
  scope: EditScope;
  operation: EditOperation;
  rows: Array<{
    product_id: number;
    expected_revision: number;
    period_id: number | null;
    values: EditValues;
  }>;
}
export const BASIC_EDIT_FIELDS = [
  ["product_name_en", "产品名称"],
  ["product_name_jp", "日文名称"],
  ["code", "产品代码"],
  ["brand", "品牌"],
  ["category_id", "类别"],
  ["supplier_id", "供应商"],
  ["country_id", "国家"],
  ["port_id", "港口"],
  ["unit", "单位"],
  ["unit_size", "单位规格"],
  ["pack_size", "包装规格"],
  ["country_of_origin", "原产地"],
] as const;
export const scopeLabel = (scope: EditScope) =>
  scope === "basic"
    ? "基本信息"
    : scope === "purchase"
      ? "采购价区间"
      : "卖价区间";

export function matchPeriodTargets(
  products: ProductItem[],
  type: "purchase" | "selling",
  from: string,
  to: string,
) {
  const targets: PeriodTarget[] = [];
  const missing: ProductItem[] = [];
  for (const product of products) {
    const periods = (product.price_periods ?? []).filter(
      (p) =>
        p.status &&
        p.price_type === type &&
        p.effective_from.slice(0, 10) === from &&
        p.effective_to.slice(0, 10) === to,
    );
    if (!periods.length) missing.push(product);
    else for (const period of periods) targets.push({ product, period });
  }
  return { targets, missing };
}

export function createEditRows(
  products: ProductItem[],
  scope: EditScope,
  operation: EditOperation,
  targets: PeriodTarget[] = [],
): EditRow[] {
  if (scope !== "basic" && operation === "edit") {
    return targets.map(({ product, period }) => {
      const values: EditValues = {
        amount: String(period.amount),
        currency: period.currency ?? product.currency ?? "",
        effective_from: period.effective_from.slice(0, 10),
        effective_to: period.effective_to.slice(0, 10),
      };
      return {
        key: `${product.id}:period:${period.id}`,
        product,
        periodId: period.id,
        values,
        original: { ...values },
      };
    });
  }
  return products.map((product) => {
    const values: EditValues =
      scope === "basic"
        ? Object.fromEntries(
            BASIC_EDIT_FIELDS.map(([key]) => [
              key,
              product[key] ?? (key.endsWith("_id") ? null : ""),
            ]),
          )
        : {
            amount: "",
            currency: product.currency ?? "",
            effective_from: "",
            effective_to: "",
          };
    return {
      key: `${product.id}:${scope === "basic" ? "basic" : "new:0"}`,
      product,
      periodId: null,
      values,
      original: { ...values },
    };
  });
}

export function applyUniformValues(
  rows: EditRow[],
  patch: EditValues,
): EditRow[] {
  return rows.map((row) => ({ ...row, values: { ...row.values, ...patch } }));
}

function isDate(value: string | number | null | undefined): boolean {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const timestamp = Date.parse(value + "T00:00:00Z");
  return (
    Number.isFinite(timestamp) &&
    new Date(timestamp).toISOString().slice(0, 10) === value
  );
}
export function rowIssues(row: EditRow, scope: EditScope): string[] {
  if (scope === "basic") {
    const issues = [];
    if (!String(row.values.product_name_en ?? "").trim())
      issues.push("产品名称不能为空");
    if (!row.values.country_id || !row.values.port_id)
      issues.push("请填写国家和港口");
    return issues;
  }
  const issues = [];
  const { amount, effective_from: from, effective_to: to } = row.values;
  if (amount === "" || amount === null || amount === undefined)
    issues.push("请填写价格");
  else if (
    !/^\d+(?:\.\d+)?$/.test(String(amount)) ||
    !Number.isFinite(Number(amount)) ||
    Number(amount) > 99999999.99
  )
    issues.push("价格必须为 0 至 99999999.99 之间的数字");
  if (!isDate(from) || !isDate(to))
    issues.push("请填写有效的开始日期和结束日期");
  else if (String(from) > String(to)) issues.push("开始日期不能晚于结束日期");
  return issues;
}

export function changedRows(
  rows: EditRow[],
  operation: EditOperation,
): EditRow[] {
  return operation === "add"
    ? rows
    : rows.filter((row) =>
        Object.keys(row.values).some(
          (key) => row.values[key] !== row.original[key],
        ),
      );
}

export function buildDirectUpdateRequest(
  products: ProductItem[],
  scope: EditScope,
  operation: EditOperation,
  rows: EditRow[],
): DirectUpdateRequest {
  const selected = new Set(products.map((p) => p.id));
  const pending = changedRows(rows, operation);
  if (!pending.length) throw new Error("没有需要保存的变更");
  for (const row of pending) {
    if (!selected.has(row.product.id))
      throw new Error("编辑行不在所选产品范围中");
    const issues = rowIssues(row, scope);
    if (issues.length)
      throw new Error(
        `${row.product.product_name_en || row.product.code}：${issues.join("；")}`,
      );
  }
  return {
    selected_product_ids: products.map((p) => p.id),
    scope,
    operation,
    rows: pending.map((row) => ({
      product_id: row.product.id,
      expected_revision: row.product.revision,
      period_id: row.periodId,
      values: Object.fromEntries(
        Object.entries(row.values)
          .filter(
            ([key, value]) =>
              operation === "add" || value !== row.original[key],
          )
          .map(([key, value]) => [
            key,
            scope === "basic" && value === "" && key !== "product_name_en"
              ? null
              : value,
          ]),
      ),
    })),
  };
}
