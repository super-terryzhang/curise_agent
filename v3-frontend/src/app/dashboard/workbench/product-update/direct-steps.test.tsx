import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { createEditRows } from "@/lib/product-batch-edit";
import type { ProductItem } from "@/lib/data-api";
import type { WorkflowBatch, WorkflowRow } from "@/lib/product-upload-api";
import { OperationStep } from "./operation-step";
import { EditDataStep } from "./edit-data-step";
import { DirectReviewStep } from "./direct-review-step";

const product = {
  id: 1,
  revision: 2,
  code: "001",
  product_name_en: "APPLE",
  country_id: 1,
  port_id: 2,
  currency: "JPY",
  price_periods: [],
} as unknown as ProductItem;
const masters = { categories: [], suppliers: [], countries: [], ports: [] };
describe("direct product editing steps", () => {
  it("distinguishes missing periods from add and blocks an empty target", () => {
    const html = renderToStaticMarkup(
      <OperationStep
        products={[product]}
        scope="purchase"
        operation="edit"
        from="2026-01-01"
        to="2026-03-31"
        onChange={vi.fn()}
        onBack={vi.fn()}
        onNext={vi.fn()}
      />,
    );
    expect(html).toContain("修改已有区间");
    expect(html).toContain("新增价格区间");
    expect(html).toContain("未找到区间的产品不会自动新增");
    expect(html).toContain("APPLE");
    expect(html).toMatch(/<button[^>]*disabled[^>]*>下一步/);
  });
  it("renders blank new prices and explicit uniform date controls without downloads", () => {
    const html = renderToStaticMarkup(
      <EditDataStep
        rows={createEditRows([product], "purchase", "add")}
        scope="purchase"
        operation="add"
        masters={masters}
        busy={false}
        onRowsChange={vi.fn()}
        onBack={vi.fn()}
        onCheck={vi.fn()}
      />,
    );
    expect(html).toContain("统一填写");
    expect(html).toContain("应用到全部 1 行");
    expect(html).toContain("再添加一个区间");
    expect(html).toContain("请填写价格");
    expect(html).not.toContain("下载");
    expect(html).toMatch(/<button[^>]*disabled[^>]*>检查并核对/);
  });
  it("shows server issue reasons with page row numbers and blocks save", () => {
    const batch = {
      id: 1,
      can_continue: false,
      summary: { total: 1, update: 0, error: 1, skip: 0, create: 0 },
    } as WorkflowBatch;
    const row = {
      staging_id: 1,
      source_row_number: 1,
      kind: "error",
      identity: { product_id: 1, product_code: "001", product_name: "APPLE" },
      fields: [],
      operations: [],
      issues: [
        { code: "OVERLAP", field: null, message: "与现有采购价区间重叠" },
      ],
      reason: null,
    } as WorkflowRow;
    const html = renderToStaticMarkup(
      <DirectReviewStep
        batch={batch}
        rows={[row]}
        busy={false}
        onBack={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    expect(html).toContain("与现有采购价区间重叠");
    expect(html).toContain("数据行");
    expect(html).not.toContain("Excel 行");
    expect(html).toMatch(/<button[^>]*disabled[^>]*>确认保存/);
  });
});
