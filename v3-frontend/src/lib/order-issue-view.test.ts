import { describe, expect, it } from "vitest";

import type { OrderIssueRow } from "./orders-api";
import {
  filterIssueRows,
  issueStatusText,
  issueSummary,
  normalizedMatchStatus,
  productResolutionHref,
  resolutionLabel,
} from "./order-issue-view";

function row(overrides: Partial<OrderIssueRow> = {}): OrderIssueRow {
  return {
    row_index: 1,
    product_code: "P-1",
    product_name: "Product",
    quantity: 1,
    unit: "EA",
    unit_price: 1,
    match_status: "matched",
    match_reason: "产品代码完全匹配",
    inquiry_disposition: "included",
    inquiry_disposition_reason: null,
    findings: [],
    ...overrides,
  };
}

describe("order issue presentation model", () => {
  const unmatched = row({ row_index: 1, match_status: "not_matched", inquiry_disposition: "excluded" });
  const matchedIssue = row({
    row_index: 2,
    inquiry_disposition: "excluded",
    findings: [{
      code: "UNIT_CONVERSION_REQUIRED", step: 7, severity: "error", scope: "row",
      message: "单位不一致", suggestion: "登记依据", evidence: {},
      resolution: { target: "unit_conversion", label: "登记换算依据" },
    }],
  });
  const warning = row({
    row_index: 3,
    inquiry_disposition: "included_with_warning",
    findings: [{
      code: "SELLING_PRICE_DEVIATION", step: 5, severity: "warning", scope: "row",
      message: "卖价偏差", suggestion: "核对", evidence: {},
      resolution: { target: "price_periods", label: "配置或核对价格期间" },
    }],
  });
  const ready = row({ row_index: 4 });
  const rows = [unmatched, matchedIssue, warning, ready];

  it("filters the four business views without reimplementing backend blocking rules", () => {
    expect(filterIssueRows(rows, "all")).toHaveLength(4);
    expect(filterIssueRows(rows, "not_matched").map((item) => item.row_index)).toEqual([1]);
    expect(filterIssueRows(rows, "matched_with_issues").map((item) => item.row_index)).toEqual([2, 3]);
    expect(filterIssueRows(rows, "inquiry_ready").map((item) => item.row_index)).toEqual([3, 4]);
  });

  it("summarizes every finding instead of hiding all but the first", () => {
    const current = row({
      findings: [
        { ...matchedIssue.findings[0], message: "单位不一致" },
        { ...warning.findings[0], message: "卖价偏差" },
      ],
    });
    expect(issueSummary(current)).toBe("2 个问题：单位不一致；卖价偏差");
  });

  it("uses the backend inquiry disposition when warning and error coexist", () => {
    const mixed = row({
      inquiry_disposition: "excluded",
      findings: [warning.findings[0], matchedIssue.findings[0]],
    });
    expect(issueStatusText(mixed)).toEqual({ match: "匹配成功", inquiry: "不进入询价" });
  });

  it("keeps unknown rules actionable with a safe fallback label", () => {
    expect(resolutionLabel({
      code: "FUTURE_RULE", step: 8, severity: "error", scope: "row",
      message: "新问题", suggestion: "人工检查", evidence: { raw: true },
      resolution: { target: "review", label: "" },
    })).toBe("查看并人工处理");
  });

  it("normalizes historical non-matched states", () => {
    expect(normalizedMatchStatus("matched")).toBe("matched");
    expect(normalizedMatchStatus("possible_match")).toBe("not_matched");
    expect(normalizedMatchStatus(undefined)).toBe("not_matched");
  });

  it("builds stable product and price deep links with enough fallback context", () => {
    const current = row({
      product_code: "A&B 10",
      matched_product: {
        id: 42, code: "MASTER-42", product_name_en: "Master", product_name_jp: null,
        price: 10, contract_price: 12, currency: "JPY", supplier_id: 3,
        category_id: null, pack_size: null, unit: "CA",
      },
    });
    expect(productResolutionHref(current, "product_master")).toBe(
      "/dashboard/data?tab=products&product=42&search=MASTER-42&action=edit",
    );
    expect(productResolutionHref(current, "price_periods")).toBe(
      "/dashboard/data?tab=products&product=42&search=MASTER-42&action=prices",
    );
    expect(productResolutionHref(row({ matched_product: undefined }), "product_master")).toBeNull();
  });
});
