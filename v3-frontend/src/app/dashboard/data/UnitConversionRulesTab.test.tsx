import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { UnitConversionRuleItem } from "@/lib/data-api";
import { RuleDetailsContent } from "./UnitConversionRulesTab";

const draftRule: UnitConversionRuleItem = {
  id: 5,
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
  status: "draft",
  evidence: "PO165047CCI 真实商品行，包装关系等待业务确认",
  valid_from: "2026-09-24",
  valid_to: null,
  verified_by: null,
  verified_at: null,
  created_by: 2,
  updated_by: 3,
  revision: 4,
  created_at: "2026-09-24T01:00:00Z",
  updated_at: "2026-09-24T02:00:00Z",
};

describe("RuleDetailsContent", () => {
  it("shows the complete current snapshot and makes draft inactivity explicit", () => {
    const html = renderToStaticMarkup(<RuleDetailsContent rule={draftRule} />);

    expect(html).toContain("草稿不会自动生效");
    expect(html).toContain("1 CA2.27 = 1 CT");
    expect(html).toContain("PO165047CCI 真实商品行，包装关系等待业务确认");
    expect(html).toContain("商品 #1820");
    expect(html).toContain("86GX12");
    expect(html).toContain("不允许拆包");
    expect(html).toContain("用户 #2");
    expect(html).toContain("用户 #3");
    expect(html).toContain("版本 4");
  });
});
