import { listProducts, type ProductItem } from "./data-api";

type Filters = Omit<NonNullable<Parameters<typeof listProducts>[0]>, "limit" | "offset">;

function excelDate(value: string | null | undefined): Date | null {
  const date = value?.slice(0, 10);
  return date ? new Date(`${date}T00:00:00`) : null;
}

/** Fetch all filtered pages before creating a file; never download partial results. */
export async function loadProductsForExport(filters: Filters): Promise<ProductItem[]> {
  const rows: ProductItem[] = [];
  const ids = new Set<number>();
  let total: number | undefined;
  do {
    const page = await listProducts({ ...filters, limit: 500, offset: rows.length });
    if (total !== undefined && total !== page.total) {
      throw new Error("导出期间产品数量发生变化，请重试");
    }
    total = page.total;
    for (const product of page.items) {
      if (ids.has(product.id)) throw new Error("导出期间产品列表发生变化，请重试");
      ids.add(product.id);
      rows.push(product);
    }
    if (rows.length > total || (!page.items.length && rows.length < total)) {
      throw new Error("未能获取完整产品列表，请重试");
    }
  } while (rows.length < total);
  return rows;
}

export async function buildPriceWorkbook(products: ProductItem[]) {
  const XLSX = await import("xlsx");
  const headers = [
    "product_name", "country", "port", "product_code", "price",
    "purchase_price_effective_from", "purchase_price_effective_to",
    "contract_price", "selling_price_effective_from", "selling_price_effective_to",
    "currency",
  ];
  const rows = products.flatMap((p) => {
    const identity = [p.product_name_en, p.country_name, p.port_name, p.code];
    const periods = (p.price_periods ?? []).filter((period) => period.status);
    if (periods.length === 0) {
      return [[...identity,
        p.price, excelDate(p.purchase_price_effective_from),
        excelDate(p.purchase_price_effective_to),
        p.contract_price, excelDate(p.selling_price_effective_from),
        excelDate(p.selling_price_effective_to), p.currency]];
    }
    return periods.map((period) => period.price_type === "purchase"
      ? [...identity, period.amount, excelDate(period.effective_from), excelDate(period.effective_to), null, null, null, period.currency || p.currency]
      : [...identity, null, null, null, period.amount, excelDate(period.effective_from), excelDate(period.effective_to), period.currency || p.currency]);
  });
  const sheet = XLSX.utils.aoa_to_sheet([headers, ...rows]);
  sheet["!cols"] = [32, 18, 20, 20, 16, 18, 18, 16, 18, 18, 12]
    .map((wch) => ({ wch }));
  for (let row = 2; row <= rows.length + 1; row++) {
    if (sheet[`D${row}`]) sheet[`D${row}`].z = "@";
    for (const col of ["E", "H"]) {
      if (sheet[`${col}${row}`]) sheet[`${col}${row}`].z = "#,##0.00";
    }
    for (const col of ["F", "G", "I", "J"]) {
      if (sheet[`${col}${row}`]) sheet[`${col}${row}`].z = "yyyy-mm-dd";
    }
  }
  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, sheet, "产品数据");
  const notes = XLSX.utils.aoa_to_sheet([
    ["产品价格更新说明"],
    ["范围：导出时当前筛选条件下的全部产品，不限当前页。"],
    ["price 为采购价；contract_price 为卖价；每个价格区间独占一行，同一产品可以出现多行。"],
    ["日期使用 YYYY-MM-DD；空白表示批量更新时保留原值。currency 为产品币种。"],
    ["修改价格后保存为 .xlsx，通过聊天上传、预览并确认提交。"],
    ["产品名称、采购国家、港口必填；产品代码请保留前导零。"],
    ["空白保留旧值，0 有效；匹配不到会新增产品，请核对预览。"],
    ["缺少国家或港口的历史产品需先补全身份信息，才能导入。"],
    ["导出不是数据库快照；提交前检查是否有其他人更新了价格。"],
  ]);
  notes["!cols"] = [{ wch: 100 }];
  XLSX.utils.book_append_sheet(workbook, notes, "使用说明");
  return workbook;
}

export async function exportProductPrices(filters: Filters): Promise<number> {
  const products = await loadProductsForExport(filters);
  if (!products.length) throw new Error("当前筛选没有可导出的产品");
  const workbook = await buildPriceWorkbook(products);
  const XLSX = await import("xlsx");
  XLSX.writeFile(workbook, `产品价格_${new Date().toISOString().slice(0, 10)}.xlsx`);
  return products.length;
}
