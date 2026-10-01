import { beforeEach, describe, expect, it, vi } from "vitest";
import * as XLSX from "xlsx";

import type { ProductItem } from "./data-api";
import { buildExistingProductUpdateWorkbook } from "./existing-product-update-workbook";
import {
  existingProductUpdateReducer,
  initialExistingProductUpdateState,
} from "./existing-product-update-workflow";
import { fetchWithAuth } from "./fetch-with-auth";
import { cancelProductBatch, type WorkflowBatch, type WorkflowRow } from "./product-upload-api";
import { visibleWorkbenchModules } from "./workbench-modules";

vi.mock("./fetch-with-auth", () => ({ fetchWithAuth: vi.fn() }));

const product = {
  id: 7,
  revision: 4,
  price_version: 2,
  product_name_en: "Synthetic Fish",
  code: "00100",
  country_name: "Japan",
  port_name: "Osaka",
  price_periods: [{
    id: 11,
    product_id: 7,
    price_type: "purchase",
    amount: 80,
    currency: "JPY",
    effective_from: "2026-01-01",
    effective_to: "2026-03-31",
    status: true,
    source: "http",
    source_batch_id: null,
    created_by: null,
    updated_by: null,
    created_at: null,
    updated_at: null,
  }],
} as ProductItem;

const batch: WorkflowBatch = {
  id: 51,
  filename: "已有产品更新.xlsx",
  file_sha256: null,
  original_available: true,
  sheet_name: "产品数据",
  header_row_number: 1,
  workflow_version: 2,
  status: "resolved",
  current_step: 3,
  total_rows: 1,
  header_diagnostics: { columns: [], unrecognized: [], duplicate_canonical: [], missing_required: [], blocking_issues: [] },
  summary: { create: 0, update: 1, skip: 0, error: 0, total: 1 },
  can_continue: true,
  created_at: null,
  committed_at: null,
};

const changedRow: WorkflowRow = {
  staging_id: 1,
  source_row_number: 2,
  kind: "update",
  identity: { product_id: 7, product_code: "00100", product_name: "Synthetic Fish" },
  operations: ["更新采购价区间"],
  fields: [{ key: "purchase_period_amount", label: "采购价区间·价格", before: 80, after: 85, action: "change", changed: true, currency: "JPY" }],
  issues: [],
  reason: null,
};

beforeEach(() => vi.clearAllMocks());

describe("existing product update local smoke path", () => {
  it("covers entry, selection, scoped workbook, validated review and cancel without commit", async () => {
    expect(visibleWorkbenchModules("employee").find((module) => module.key === "product-update")?.href)
      .toBe("/dashboard/workbench/product-update");

    let state = initialExistingProductUpdateState();
    state = existingProductUpdateReducer(state, { type: "select_product", product, selected: true });
    state = existingProductUpdateReducer(state, { type: "continue_products" });
    state = existingProductUpdateReducer(state, { type: "set_scope", scope: { basic: false, purchase: true, selling: false } });
    state = existingProductUpdateReducer(state, { type: "continue_scope" });
    expect(state.stage).toBe(3);

    const workbook = await buildExistingProductUpdateWorkbook([product], state.scope);
    const rows = XLSX.utils.sheet_to_json<unknown[]>(workbook.Sheets["产品数据"], { header: 1 });
    expect(rows.flat()).toContain(11);
    expect(rows).toHaveLength(3);

    state = existingProductUpdateReducer(state, { type: "upload_started" });
    state = existingProductUpdateReducer(state, { type: "validation_succeeded", batch, rows: [changedRow] });
    expect(state.stage).toBe(5);
    expect(state.completed).toBe(false);
    expect(state.rows[0].fields[0].after).toBe(85);

    vi.mocked(fetchWithAuth).mockResolvedValue(new Response("null", { status: 200, headers: { "Content-Type": "application/json" } }));
    await cancelProductBatch(batch.id);
    expect(fetchWithAuth).toHaveBeenCalledWith(expect.stringContaining("/batches/51/cancel"), { method: "POST" });
  });
});
