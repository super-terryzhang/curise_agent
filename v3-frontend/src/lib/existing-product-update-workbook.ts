import type { ProductItem } from "./data-api";
import { buildProductWorkbook } from "./export-products";

export interface ExistingProductUpdateScope {
  basic: boolean;
  purchase: boolean;
  selling: boolean;
}

function validateRequest(
  products: ProductItem[],
  scope: ExistingProductUpdateScope,
) {
  if (!products.length) throw new Error("请至少选择一个产品");
  if (!scope.basic && !scope.purchase && !scope.selling) {
    throw new Error("请至少选择一个更新范围");
  }
}

function scopeText(scope: ExistingProductUpdateScope): string {
  return [
    scope.basic && "基本信息",
    scope.purchase && "采购价区间",
    scope.selling && "卖价区间",
  ].filter(Boolean).join("、");
}

export async function buildExistingProductUpdateWorkbook(
  products: ProductItem[],
  scope: ExistingProductUpdateScope,
) {
  validateRequest(products, scope);
  return buildProductWorkbook(products, {
    ...scope,
    hideSystemColumns: true,
    notes: [
      ["已有产品更新说明"],
      [`本文件包含 ${products.length} 个已选择产品；更新范围：${scopeText(scope)}。`],
      ["产品数据只在第一行修改；每个采购价或卖价区间独占一行。"],
      ["日期使用 YYYY-MM-DD；空白表示保留原值，0 是有效价格。"],
      ["系统匹配列已隐藏，请勿删除、改名或修改。"],
      ["价格区间 ID 为空表示新增区间；保留 ID 表示更新该区间。"],
      ["提交前系统会再次检查价格区间重叠和产品版本。"],
    ],
  });
}

export async function downloadExistingProductUpdateWorkbook(
  products: ProductItem[],
  scope: ExistingProductUpdateScope,
  date = new Date().toISOString().slice(0, 10),
): Promise<number> {
  const workbook = await buildExistingProductUpdateWorkbook(products, scope);
  const XLSX = await import("xlsx");
  XLSX.writeFile(workbook, `已有产品更新_${date}.xlsx`);
  return products.length;
}
