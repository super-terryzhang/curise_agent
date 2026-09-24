import type {
  UnitConversionRuleItem,
  UnitConversionRuleScope,
  UnitConversionRuleStatus,
} from "./data-api";

export interface UnitConversionRuleFilters {
  status: UnitConversionRuleStatus | "all";
  scope: UnitConversionRuleScope | "all";
  query: string;
}

export function summarizeUnitConversionRules(rules: UnitConversionRuleItem[]) {
  return rules.reduce(
    (summary, rule) => {
      summary.total += 1;
      summary[rule.status] += 1;
      return summary;
    },
    { total: 0, draft: 0, verified: 0, retired: 0 },
  );
}

export function unitConversionFormula(rule: UnitConversionRuleItem): string {
  return `${rule.source_quantity} ${rule.source_unit} = ${rule.target_quantity} ${rule.target_unit}`;
}

export function filterUnitConversionRules(
  rules: UnitConversionRuleItem[],
  filters: UnitConversionRuleFilters,
): UnitConversionRuleItem[] {
  const query = filters.query.trim().toLocaleLowerCase();
  return rules.filter((rule) => {
    if (filters.status !== "all" && rule.status !== filters.status) return false;
    if (filters.scope !== "all" && rule.scope_type !== filters.scope) return false;
    if (!query) return true;
    return [
      rule.id,
      rule.product_id,
      rule.source_system,
      rule.source_unit,
      rule.target_unit,
      rule.evidence,
      rule.pack_signature,
    ]
      .filter((value) => value !== null && value !== undefined)
      .some((value) => String(value).toLocaleLowerCase().includes(query));
  });
}
