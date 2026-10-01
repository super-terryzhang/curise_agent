import type { ProductItem } from "./data-api";
import type {
  CommitResult,
  WorkflowBatch,
  WorkflowRow,
} from "./product-upload-api";
import type { ExistingProductUpdateScope } from "./existing-product-update-workbook";

export type ExistingProductUpdateStage = 1 | 2 | 3 | 4 | 5;

export interface ExistingProductUpdateState {
  stage: ExistingProductUpdateStage;
  selectedProducts: Record<number, ProductItem>;
  scope: ExistingProductUpdateScope;
  batch: WorkflowBatch | null;
  rows: WorkflowRow[];
  result: CommitResult | null;
  completed: boolean;
  error: string | null;
}

export type ExistingProductUpdateAction =
  | { type: "select_product"; product: ProductItem; selected: boolean }
  | { type: "clear_products" }
  | { type: "continue_products" }
  | { type: "set_scope"; scope: ExistingProductUpdateScope }
  | { type: "continue_scope" }
  | { type: "go_back"; stage: ExistingProductUpdateStage }
  | { type: "upload_started" }
  | { type: "validation_failed"; batch: WorkflowBatch; rows: WorkflowRow[] }
  | { type: "validation_succeeded"; batch: WorkflowBatch; rows: WorkflowRow[] }
  | { type: "commit_failed"; error: string }
  | { type: "commit_succeeded"; result: CommitResult }
  | { type: "reset" };

const EMPTY_SCOPE: ExistingProductUpdateScope = {
  basic: false,
  purchase: false,
  selling: false,
};

export function initialExistingProductUpdateState(
  selectedProducts: Record<number, ProductItem> = {},
): ExistingProductUpdateState {
  return {
    stage: 1,
    selectedProducts,
    scope: { ...EMPTY_SCOPE },
    batch: null,
    rows: [],
    result: null,
    completed: false,
    error: null,
  };
}

export function existingProductUpdateReducer(
  state: ExistingProductUpdateState,
  action: ExistingProductUpdateAction,
): ExistingProductUpdateState {
  switch (action.type) {
    case "select_product": {
      const selectedProducts = { ...state.selectedProducts };
      if (action.selected) selectedProducts[action.product.id] = action.product;
      else delete selectedProducts[action.product.id];
      return { ...state, selectedProducts, error: null };
    }
    case "clear_products":
      return { ...state, selectedProducts: {}, error: null };
    case "continue_products":
      return Object.keys(state.selectedProducts).length
        ? { ...state, stage: 2, error: null }
        : { ...state, error: "请至少选择一个产品" };
    case "set_scope":
      return { ...state, scope: action.scope, error: null };
    case "continue_scope":
      return actionHasScope(state.scope)
        ? { ...state, stage: 3, error: null }
        : { ...state, error: "请至少选择一个更新范围" };
    case "go_back":
      return { ...state, stage: action.stage, error: null };
    case "upload_started":
      return { ...state, stage: 4, rows: [], batch: null, result: null, completed: false, error: null };
    case "validation_failed":
      return { ...state, stage: 4, batch: action.batch, rows: action.rows, error: null };
    case "validation_succeeded":
      if (action.rows.some((item) => item.kind === "create")) {
        return {
          ...state,
          stage: 4,
          batch: action.batch,
          rows: action.rows,
          error: "文件包含新增产品，请改用“产品数据上传”处理新增产品",
        };
      }
      return { ...state, stage: 5, batch: action.batch, rows: action.rows, error: null };
    case "commit_failed":
      return { ...state, stage: 5, error: action.error };
    case "commit_succeeded":
      return { ...state, stage: 5, result: action.result, completed: true, error: null };
    case "reset":
      return initialExistingProductUpdateState();
  }
}

function actionHasScope(scope: ExistingProductUpdateScope): boolean {
  return scope.basic || scope.purchase || scope.selling;
}

export function operationSummary(rows: WorkflowRow[]): Record<string, number> {
  const summary: Record<string, number> = {};
  for (const row of rows) {
    for (const operation of new Set(row.operations)) {
      summary[operation] = (summary[operation] ?? 0) + 1;
    }
  }
  return summary;
}

export interface FlattenedChange {
  stagingId: number;
  sourceRowNumber: number;
  productCode: string | null;
  productName: string | null;
  operation: string;
  field: string;
  before: unknown;
  after: unknown;
  currency: string | null;
}

export function flattenChangedFields(rows: WorkflowRow[]): FlattenedChange[] {
  return rows.flatMap((row) => row.fields
    .filter((field) => field.changed)
    .map((field) => ({
      stagingId: row.staging_id,
      sourceRowNumber: row.source_row_number,
      productCode: row.identity.product_code,
      productName: row.identity.product_name,
      operation: operationForField(row.operations, field.key),
      field: field.label,
      before: field.before,
      after: field.after,
      currency: field.currency,
    })));
}

function operationForField(operations: string[], fieldKey: string): string {
  const priceType = fieldKey.startsWith("purchase_")
    ? "采购价"
    : fieldKey.startsWith("selling_") ? "卖价" : null;
  return operations.find((operation) => priceType && operation.includes(priceType))
    ?? operations[0]
    ?? "更新产品";
}

export function issueSuggestion(issue: { code: string; field: string | null; message: string }): string {
  if (issue.code.includes("overlap") || issue.message.includes("重叠")) {
    return "调整开始日期或结束日期";
  }
  if (issue.field?.endsWith("_id") || issue.message.includes(" ID")) {
    return "使用导出文件中的原始 ID";
  }
  if (issue.field === "price" || issue.field === "contract_price" || issue.message.includes("价格")) {
    return "修改价格后重新上传";
  }
  return "按问题说明修改 Excel 后重新上传";
}
