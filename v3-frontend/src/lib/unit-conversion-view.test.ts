import { describe, expect, it } from "vitest";

import {
  buildConversionRequest,
  conversionScopeOptions,
  type UnitConversionContext,
} from "./unit-conversion-view";

const context: UnitConversionContext = {
  productCode: "99PRD010804",
  productName: "Mozzarella Cheese",
  sourceUnit: "CA2.27",
  targetUnit: "CT",
  productUnit: "CT",
  unitSize: "86gX12",
  packSize: null,
};

describe("conversionScopeOptions", () => {
  it.each(["employee", "finance"])(
    "keeps reusable scopes hidden from %s",
    (role) => {
      expect(conversionScopeOptions(role, context).map((option) => option.value)).toEqual([
        "order_row",
      ]);
    },
  );

  it.each(["admin", "superadmin"])(
    "shows all explicit scopes to %s without preselecting broad reuse",
    (role) => {
      const options = conversionScopeOptions(role, context);
      expect(options.map((option) => option.value)).toEqual([
        "order_row",
        "product",
        "source_unit",
      ]);
      expect(options[0].value).toBe("order_row");
    },
  );

  it("names the exact product pack and exact unit pair without wildcards", () => {
    const options = conversionScopeOptions("admin", context);
    const product = options.find((option) => option.value === "product");
    const source = options.find((option) => option.value === "source_unit");

    expect(product?.label).toContain("99PRD010804");
    expect(product?.description).toContain("CT / 86gX12");
    expect(source?.label).toContain("CA2.27 → CT");
    expect(source?.label).not.toContain("*");
    expect(source?.description).toContain("完全相同");
  });
});

describe("buildConversionRequest", () => {
  it("builds the existing one-row request without reusable fields", () => {
    expect(
      buildConversionRequest({
        scope: "order_row",
        sourceQuantity: "9",
        sourceUnit: "CA24.0",
        rfqQuantity: "10",
        rfqUnit: "CA",
        evidence: "供应商邮件确认",
        ruleSourceQuantity: "",
        ruleTargetQuantity: "",
        targetStep: "",
        breakPack: null,
      }),
    ).toEqual({
      action: "record_conversion",
      conversion_scope: "order_row",
      source_quantity: "9",
      source_unit: "CA24.0",
      rfq_quantity: "10",
      rfq_unit: "CA",
      evidence: "供应商邮件确认",
    });
  });

  it("requires a positive reusable basis and evidence", () => {
    expect(() =>
      buildConversionRequest({
        scope: "product",
        sourceQuantity: "9",
        sourceUnit: "CA24.0",
        rfqQuantity: "10",
        rfqUnit: "CA",
        evidence: "",
        ruleSourceQuantity: "",
        ruleTargetQuantity: "10",
        targetStep: "1",
        breakPack: null,
      }),
    ).toThrow("确认依据");

    expect(() =>
      buildConversionRequest({
        scope: "product",
        sourceQuantity: "9",
        sourceUnit: "CA24.0",
        rfqQuantity: "10",
        rfqUnit: "CA",
        evidence: "已确认",
        ruleSourceQuantity: "0",
        ruleTargetQuantity: "10",
        targetStep: "1",
        breakPack: null,
      }),
    ).toThrow("换算关系");
  });

  it("builds an exact reusable relationship with tri-state pack evidence", () => {
    expect(
      buildConversionRequest({
        scope: "source_unit",
        sourceQuantity: "12",
        sourceUnit: "CA2.27",
        rfqQuantity: "12",
        rfqUnit: "CT",
        evidence: "供应商确认同一包装",
        ruleSourceQuantity: "1",
        ruleTargetQuantity: "1",
        targetStep: "1",
        breakPack: false,
      }),
    ).toEqual({
      action: "record_conversion",
      conversion_scope: "source_unit",
      source_quantity: "12",
      source_unit: "CA2.27",
      rfq_quantity: "12",
      rfq_unit: "CT",
      evidence: "供应商确认同一包装",
      rule_source_quantity: "1",
      rule_target_quantity: "1",
      target_step: "1",
      break_pack: false,
    });
  });

  it("submits exact decimal text without JavaScript Number rounding", () => {
    const exact = "0.10000000000000000001";

    const request = buildConversionRequest({
      scope: "product",
      sourceQuantity: exact,
      sourceUnit: "EA",
      rfqQuantity: exact,
      rfqUnit: "CT",
      evidence: "精确数量已人工确认",
      ruleSourceQuantity: exact,
      ruleTargetQuantity: exact,
      targetStep: exact,
      breakPack: null,
    });

    expect(request.source_quantity).toBe(exact);
    expect(request.rfq_quantity).toBe(exact);
    expect(request.rule_source_quantity).toBe(exact);
    expect(request.rule_target_quantity).toBe(exact);
    expect(request.target_step).toBe(exact);
  });
});
