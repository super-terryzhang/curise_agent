import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { CommitResult, WorkflowBatch, WorkflowRow } from "@/lib/product-upload-api";
import { CompletionStep } from "./completion-step";
import { ReviewStep } from "./review-step";
import { ValidationStep } from "./validation-step";

function batch(overrides: Partial<WorkflowBatch> = {}): WorkflowBatch {
  return {
    id: 91,
    filename: "已有产品更新.xlsx",
    file_sha256: "abc",
    original_available: true,
    sheet_name: "产品数据",
    header_row_number: 8,
    workflow_version: 1,
    status: "resolved",
    current_step: 2,
    total_rows: 1,
    header_diagnostics: {
      columns: [], unrecognized: [], duplicate_canonical: [], missing_required: [], blocking_issues: [],
    },
    summary: { create: 0, update: 1, skip: 0, error: 0, total: 1 },
    can_continue: true,
    created_at: "2026-10-01T01:00:00Z",
    committed_at: null,
    ...overrides,
  };
}

function row(overrides: Partial<WorkflowRow> = {}): WorkflowRow {
  return {
    staging_id: 12,
    source_row_number: 9,
    kind: "update",
    identity: { product_id: 18, product_code: "001258", product_name: "APPLE GRANNY SMITH" },
    operations: ["更新产品", "新增采购价区间"],
    fields: [
      { key: "brand", label: "品牌", before: "OLD", after: "NEW", action: "update", changed: true, currency: null },
      { key: "purchase_price", label: "采购价", before: 320, after: 340, action: "create", changed: true, currency: "JPY" },
    ],
    issues: [],
    reason: null,
    ...overrides,
  };
}

describe("existing product update result steps", () => {
  it("shows the exact row-level validation reason and a conservative suggestion", () => {
    const invalidRow = row({
      kind: "error",
      issues: [{ code: "purchase_period_overlap", field: "purchase_price_effective_from", message: "采购价区间与已有区间重叠" }],
      reason: "采购价区间与已有区间重叠",
    });
    const html = renderToStaticMarkup(
      <ValidationStep
        batch={batch({ can_continue: false, summary: { create: 0, update: 0, skip: 0, error: 1, total: 1 } })}
        rows={[invalidRow]}
        validating={false}
        error={null}
        onBack={vi.fn()}
        onRetry={vi.fn()}
        onContinue={vi.fn()}
      />,
    );

    expect(html).toContain("Excel 第 9 行");
    expect(html).toContain("001258");
    expect(html).toContain("采购价区间与已有区间重叠");
    expect(html).toContain("调整开始日期或结束日期");
    expect(html).toContain("返回修改并重新上传");
    expect(html).not.toContain("提交更新");
  });

  it("shows a passed result and allows the user to enter review", () => {
    const html = renderToStaticMarkup(
      <ValidationStep
        batch={batch()}
        rows={[row()]}
        validating={false}
        error={null}
        onBack={vi.fn()}
        onRetry={vi.fn()}
        onContinue={vi.fn()}
      />,
    );

    expect(html).toContain("程序检查通过");
    expect(html).toContain("1 行更新");
    expect(html).toContain("核对变更");
  });

  it("renders compact before/after changes and an explicit version-warning confirmation", () => {
    const html = renderToStaticMarkup(
      <ReviewStep
        batch={batch()}
        rows={[row()]}
        committing={false}
        error={null}
        confirmOpen
        onConfirmOpenChange={vi.fn()}
        onBack={vi.fn()}
        onCommit={vi.fn()}
      />,
    );

    expect(html).toContain("变更前");
    expect(html).toContain("变更后");
    expect(html).toContain("OLD");
    expect(html).toContain("NEW");
    expect(html).toContain("320 JPY");
    expect(html).toContain("340 JPY");
    expect(html).toContain("提交时会再次检查产品版本和价格区间");
    expect(html).toContain("确认提交更新");
  });

  it("blocks a create row and renders the committed result with history", () => {
    const createHtml = renderToStaticMarkup(
      <ReviewStep
        batch={batch({ summary: { create: 1, update: 0, skip: 0, error: 0, total: 1 } })}
        rows={[row({ kind: "create", identity: { product_id: null, product_code: "NEW-1", product_name: "NEW PRODUCT" } })]}
        committing={false}
        error={null}
        confirmOpen={false}
        onConfirmOpenChange={vi.fn()}
        onBack={vi.fn()}
        onCommit={vi.fn()}
      />,
    );
    expect(createHtml).toContain("文件包含新增产品");
    expect(createHtml).toContain("产品数据上传");
    expect(createHtml).toMatch(/<button[^>]*disabled[^>]*>提交更新/);

    const result: CommitResult = { created: 0, updated: 3, skipped: 1, errors: 0, error_details: [] };
    const completeHtml = renderToStaticMarkup(<CompletionStep result={result} onReset={vi.fn()} showHistory />);
    expect(completeHtml).toContain("更新已完成");
    expect(completeHtml).toContain("已更新");
    expect(completeHtml).toContain("3");
    expect(completeHtml).toContain("最近上传");
    expect(completeHtml).toContain("继续更新其他产品");
  });
});
