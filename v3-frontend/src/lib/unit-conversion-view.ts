import type { OrderRowResolveRequest } from "./orders-api";

export type ConversionScope = "order_row" | "product" | "source_unit";

export interface UnitConversionContext {
  productCode?: string | null;
  productName?: string | null;
  sourceUnit?: string | null;
  targetUnit?: string | null;
  productUnit?: string | null;
  unitSize?: string | null;
  packSize?: string | null;
}

export interface ConversionScopeOption {
  value: ConversionScope;
  label: string;
  description: string;
}

function display(value: string | null | undefined, fallback = "未填写") {
  return value?.trim() || fallback;
}

export function conversionScopeOptions(
  role: string | null | undefined,
  context: UnitConversionContext,
): ConversionScopeOption[] {
  const options: ConversionScopeOption[] = [
    {
      value: "order_row",
      label: "仅处理当前订单行",
      description: "只保存本次人工确认，不影响其他订单。",
    },
  ];
  if (role !== "admin" && role !== "superadmin") return options;

  const productIdentity = [context.productCode, context.productName]
    .map((value) => value?.trim())
    .filter(Boolean)
    .join(" · ");
  const pack = [context.productUnit, context.unitSize, context.packSize]
    .map((value) => value?.trim())
    .filter(Boolean)
    .join(" / ");
  const sourceUnit = display(context.sourceUnit);
  const targetUnit = display(context.targetUnit);
  options.push(
    {
      value: "product",
      label: `保存给相同商品：${productIdentity || "当前匹配商品"}`,
      description: `仅在商品及包装完全一致时复用（${pack || "包装信息为空"}）。`,
    },
    {
      value: "source_unit",
      label: `保存为精确单位规则：${sourceUnit} → ${targetUnit}`,
      description: `会影响来源系统中单位组合完全相同的商品，不做前缀或模糊匹配。`,
    },
  );
  return options;
}

export interface ConversionFormValues {
  scope: ConversionScope;
  sourceQuantity: string;
  sourceUnit: string;
  rfqQuantity: string;
  rfqUnit: string;
  evidence: string;
  ruleSourceQuantity: string;
  ruleTargetQuantity: string;
  targetStep: string;
  breakPack: boolean | null;
}

function positiveNumber(value: string, label: string): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${label}必须是大于 0 的数字`);
  }
  return parsed;
}

export function buildConversionRequest(
  values: ConversionFormValues,
): OrderRowResolveRequest {
  const evidence = values.evidence.trim();
  if (!evidence) throw new Error("请填写人工确认依据");
  const sourceUnit = values.sourceUnit.trim();
  const rfqUnit = values.rfqUnit.trim();
  if (!sourceUnit || !rfqUnit) throw new Error("原订购单位和询价单位不能为空");

  const request: OrderRowResolveRequest = {
    action: "record_conversion",
    conversion_scope: values.scope,
    source_quantity: positiveNumber(values.sourceQuantity, "原订购数量"),
    source_unit: sourceUnit,
    rfq_quantity: positiveNumber(values.rfqQuantity, "询价数量"),
    rfq_unit: rfqUnit,
    evidence,
  };
  if (values.scope === "order_row") return request;

  request.rule_source_quantity = positiveNumber(
    values.ruleSourceQuantity,
    "换算关系左侧数量",
  );
  request.rule_target_quantity = positiveNumber(
    values.ruleTargetQuantity,
    "换算关系右侧数量",
  );
  if (values.targetStep.trim()) {
    request.target_step = positiveNumber(values.targetStep, "供应商订购步长");
  }
  request.break_pack = values.breakPack;
  return request;
}
