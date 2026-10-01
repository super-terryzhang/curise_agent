import { describe, expect, it } from "vitest";

import type { ProductItem } from "./data-api";
import type { CommitResult, WorkflowBatch, WorkflowRow } from "./product-upload-api";
import {
  existingProductUpdateReducer,
  flattenChangedFields,
  initialExistingProductUpdateState,
  issueSuggestion,
  operationSummary,
} from "./existing-product-update-workflow";

const product = (id: number) => ({ id, product_name_en: `Product ${id}` } as ProductItem);

const batch = (canContinue: boolean): WorkflowBatch => ({
  id: 8,
  filename: "update.xlsx",
  file_sha256: null,
  original_available: true,
  sheet_name: "产品数据",
  header_row_number: 1,
  workflow_version: 2,
  status: "resolved",
  current_step: canContinue ? 3 : 2,
  total_rows: 2,
  header_diagnostics: { columns: [], unrecognized: [], duplicate_canonical: [], missing_required: [], blocking_issues: [] },
  summary: { create: 0, update: 1, skip: 1, error: canContinue ? 0 : 1, total: 2 },
  can_continue: canContinue,
  created_at: null,
  committed_at: null,
});

const row = (overrides: Partial<WorkflowRow> = {}): WorkflowRow => ({
  staging_id: 1,
  source_row_number: 2,
  kind: "update",
  identity: { product_id: 7, product_code: "00100", product_name: "Synthetic Fish" },
  operations: ["更新采购价区间"],
  fields: [{ key: "purchase_period_amount", label: "采购价区间·价格", before: 80, after: 90, action: "change", changed: true, currency: "JPY" }],
  issues: [],
  reason: null,
  ...overrides,
});

describe("existing product update workflow", () => {
  it("persists selected products by id and guards empty product selection", () => {
    let state = existingProductUpdateReducer(initialExistingProductUpdateState(), { type: "continue_products" });
    expect(state.stage).toBe(1);
    expect(state.error).toBe("请至少选择一个产品");

    state = existingProductUpdateReducer(state, { type: "select_product", product: product(7), selected: true });
    state = existingProductUpdateReducer(state, { type: "select_product", product: product(8), selected: true });
    state = existingProductUpdateReducer(state, { type: "select_product", product: product(7), selected: false });
    expect(Object.keys(state.selectedProducts)).toEqual(["8"]);
    expect(existingProductUpdateReducer(state, { type: "continue_products" }).stage).toBe(2);
  });

  it("guards empty scope and advances through download and validation", () => {
    let state = initialExistingProductUpdateState({ 7: product(7) });
    state = existingProductUpdateReducer(state, { type: "continue_products" });
    state = existingProductUpdateReducer(state, { type: "continue_scope" });
    expect(state.stage).toBe(2);
    expect(state.error).toBe("请至少选择一个更新范围");

    state = existingProductUpdateReducer(state, { type: "set_scope", scope: { basic: false, purchase: true, selling: false } });
    state = existingProductUpdateReducer(state, { type: "continue_scope" });
    expect(state.stage).toBe(3);
    state = existingProductUpdateReducer(state, { type: "upload_started" });
    expect(state.stage).toBe(4);
  });

  it("keeps failed validation in stage four and moves valid changes to stage five", () => {
    const failed = existingProductUpdateReducer(initialExistingProductUpdateState(), {
      type: "validation_failed",
      batch: batch(false),
      rows: [row({ kind: "error", issues: [{ code: "overlap", field: "purchase_price_effective_from", message: "区间重叠" }] })],
    });
    expect(failed.stage).toBe(4);
    expect(failed.rows).toHaveLength(1);

    const valid = existingProductUpdateReducer(initialExistingProductUpdateState(), {
      type: "validation_succeeded",
      batch: batch(true),
      rows: [row()],
    });
    expect(valid.stage).toBe(5);
    expect(valid.error).toBeNull();
  });

  it("blocks rows classified as new products in the dedicated update flow", () => {
    const state = existingProductUpdateReducer(initialExistingProductUpdateState(), {
      type: "validation_succeeded",
      batch: batch(true),
      rows: [row({ kind: "create", operations: ["新增产品"] })],
    });
    expect(state.stage).toBe(4);
    expect(state.error).toContain("新增产品");
  });

  it("preserves review data on commit conflict and records completion", () => {
    let state = existingProductUpdateReducer(initialExistingProductUpdateState(), {
      type: "validation_succeeded",
      batch: batch(true),
      rows: [row()],
    });
    state = existingProductUpdateReducer(state, { type: "commit_failed", error: "产品版本冲突" });
    expect(state.stage).toBe(5);
    expect(state.rows).toHaveLength(1);
    expect(state.error).toBe("产品版本冲突");

    const result: CommitResult = { created: 0, updated: 1, skipped: 1, errors: 0, error_details: [] };
    state = existingProductUpdateReducer(state, { type: "commit_succeeded", result });
    expect(state.completed).toBe(true);
    expect(state.result).toEqual(result);
  });
});

describe("existing product update display helpers", () => {
  it("summarizes operations and flattens changed fields", () => {
    const rows = [
      row(),
      row({ staging_id: 2, operations: ["新增卖价区间"], fields: [{ key: "selling_period_amount", label: "卖价区间·价格", before: null, after: 140, action: "set_new", changed: true, currency: "JPY" }] }),
    ];
    expect(operationSummary(rows)).toEqual({ "更新采购价区间": 1, "新增卖价区间": 1 });
    expect(flattenChangedFields(rows)).toEqual([
      expect.objectContaining({ productCode: "00100", operation: "更新采购价区间", before: 80, after: 90 }),
      expect.objectContaining({ operation: "新增卖价区间", before: null, after: 140 }),
    ]);
  });

  it("provides conservative suggestions and preserves unknown issue text", () => {
    expect(issueSuggestion({ code: "period_overlap", field: "purchase_price_effective_from", message: "采购价区间重叠" }))
      .toBe("调整开始日期或结束日期");
    expect(issueSuggestion({ code: "unexpected", field: null, message: "未知业务问题" }))
      .toBe("按问题说明修改 Excel 后重新上传");
  });
});
