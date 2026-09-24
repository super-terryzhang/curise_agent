import { describe, expect, it } from "vitest";

import type { UnitConversionRuleItem } from "./data-api";
import {
  filterUnitConversionRules,
  summarizeUnitConversionRules,
  unitConversionFormula,
} from "./unit-conversion-rules-view";

const rules: UnitConversionRuleItem[] = [
  {
    id: 1,
    scope_type: "source_unit",
    product_id: null,
    source_system: "oracle_po",
    source_unit: "KG2.2",
    target_unit: "KG",
    source_quantity: "1",
    target_quantity: "2.2",
    target_step: null,
    break_pack: null,
    pack_signature: null,
    status: "draft",
    evidence: "PO165047CCI 中出现 46 次，业务含义待确认",
    valid_from: null,
    valid_to: null,
    verified_by: null,
    verified_at: null,
    created_by: 2,
    updated_by: 2,
    revision: 2,
    created_at: "2026-09-24T01:00:00Z",
    updated_at: "2026-09-24T02:00:00Z",
  },
  {
    id: 2,
    scope_type: "product",
    product_id: 1820,
    source_system: "oracle_po",
    source_unit: "CA2.27",
    target_unit: "CT",
    source_quantity: "1",
    target_quantity: "1",
    target_step: "1",
    break_pack: false,
    pack_signature: '["CT","","86GX12"]',
    status: "verified",
    evidence: "供应商书面确认包装相同",
    valid_from: "2026-09-24",
    valid_to: null,
    verified_by: 1,
    verified_at: "2026-09-24T03:00:00Z",
    created_by: 2,
    updated_by: 1,
    revision: 2,
    created_at: "2026-09-24T01:00:00Z",
    updated_at: "2026-09-24T03:00:00Z",
  },
  {
    id: 3,
    scope_type: "source_unit",
    product_id: null,
    source_system: "oracle_po",
    source_unit: "CA22.0",
    target_unit: "CA",
    source_quantity: "1",
    target_quantity: "1",
    target_step: null,
    break_pack: null,
    pack_signature: null,
    status: "retired",
    evidence: "旧包装",
    valid_from: null,
    valid_to: "2026-09-23",
    verified_by: null,
    verified_at: null,
    created_by: 2,
    updated_by: 2,
    revision: 3,
    created_at: "2026-09-24T01:00:00Z",
    updated_at: "2026-09-24T04:00:00Z",
  },
];

describe("unit conversion rule presentation", () => {
  it("summarizes every lifecycle state without treating drafts as active", () => {
    expect(summarizeUnitConversionRules(rules)).toEqual({
      total: 3,
      draft: 1,
      verified: 1,
      retired: 1,
    });
  });

  it("formats the exact stored quantities without Number rounding", () => {
    expect(
      unitConversionFormula({
        ...rules[0],
        source_quantity: "0.10000000000000000001",
        target_quantity: "2.20000000000000000001",
      }),
    ).toBe("0.10000000000000000001 KG2.2 = 2.20000000000000000001 KG");
  });

  it("filters by status, scope, and free text across evidence and product id", () => {
    expect(filterUnitConversionRules(rules, { status: "verified", scope: "all", query: "" }).map((rule) => rule.id)).toEqual([2]);
    expect(filterUnitConversionRules(rules, { status: "all", scope: "product", query: "1820" }).map((rule) => rule.id)).toEqual([2]);
    expect(filterUnitConversionRules(rules, { status: "all", scope: "all", query: "书面确认" }).map((rule) => rule.id)).toEqual([2]);
  });
});
