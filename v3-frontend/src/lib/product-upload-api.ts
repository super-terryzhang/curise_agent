import { fetchWithAuth } from "./fetch-with-auth";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

export interface HeaderIssue {
  code: string;
  field: string | null;
  columns: number[];
  message: string;
}

export interface UploadSummary {
  create: number;
  update: number;
  skip: number;
  error: number;
  total: number;
}

export interface WorkflowBatch {
  id: number;
  filename: string;
  file_sha256: string | null;
  original_available: boolean;
  sheet_name: string | null;
  header_row_number: number | null;
  workflow_version: number;
  status: string;
  current_step: number;
  total_rows: number;
  header_diagnostics: {
    columns: Array<{
      column: number;
      raw: string;
      canonical: string | null;
      status: string;
    }>;
    unrecognized: Array<{
      column: number;
      raw: string;
      canonical: null;
      status: string;
    }>;
    duplicate_canonical: Array<{ canonical: string; columns: number[] }>;
    missing_required: string[];
    blocking_issues: HeaderIssue[];
  };
  summary: UploadSummary;
  can_continue: boolean;
  created_at: string | null;
  committed_at: string | null;
}

export interface WorkflowField {
  key: string;
  label: string;
  before: unknown;
  after: unknown;
  action: string | null;
  changed: boolean;
  currency: string | null;
}

export interface WorkflowRow {
  staging_id: number;
  source_row_number: number;
  kind: "error" | "create" | "update" | "skip";
  identity: {
    product_id: number | null;
    product_code: string | null;
    product_name: string | null;
  };
  operations: string[];
  fields: WorkflowField[];
  issues: Array<{ code: string; field: string | null; message: string }>;
  reason: string | null;
}

export interface WorkflowRowsPage {
  items: WorkflowRow[];
  page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
  summary: UploadSummary;
}

export interface CommitResult {
  created: number;
  updated: number;
  skipped: number;
  errors: number;
  error_details: Array<Record<string, unknown>>;
}

export interface WorkflowBatchPage {
  items: WorkflowBatch[];
  page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (response.ok) return response.json() as Promise<T>;
  let message = `请求失败 (${response.status})`;
  try {
    const body = (await response.json()) as { detail?: string };
    if (body.detail) message = body.detail;
  } catch {
    // Keep the status-based fallback when the proxy returns non-JSON.
  }
  throw new Error(message);
}

export function productUploadTemplateUrl(): string {
  return `${API_BASE}/api/data-upload/template`;
}

export async function uploadProductWorkbook(
  file: File,
): Promise<WorkflowBatch> {
  const form = new FormData();
  form.append("file", file);
  return parseResponse(
    await fetchWithAuth(
      `${API_BASE}/api/data-upload/workbench/products/upload`,
      {
        method: "POST",
        body: form,
        timeout: 120000,
      },
    ),
  );
}

export async function validateProductBatch(
  batchId: number,
): Promise<WorkflowBatch> {
  return parseResponse(
    await fetchWithAuth(
      `${API_BASE}/api/data-upload/workbench/batches/${batchId}/validate`,
      {
        method: "POST",
        timeout: 120000,
      },
    ),
  );
}

export async function getProductBatchRows(
  batchId: number,
  options: {
    view: "all" | "issues" | "changes";
    page: number;
    pageSize?: number;
    changedOnly?: boolean;
  },
): Promise<WorkflowRowsPage> {
  const params = new URLSearchParams({
    view: options.view,
    page: String(options.page),
    page_size: String(options.pageSize ?? 25),
    changed_only: String(options.changedOnly ?? true),
  });
  return parseResponse(
    await fetchWithAuth(
      `${API_BASE}/api/data-upload/workbench/batches/${batchId}/rows?${params}`,
    ),
  );
}

export async function loadAllProductBatchRows(
  batchId: number,
  view: "all" | "issues" | "changes",
  changedOnly = true,
): Promise<WorkflowRowsPage> {
  const first = await getProductBatchRows(batchId, {
    view,
    page: 1,
    pageSize: 200,
    changedOnly,
  });
  const items = [...first.items];
  for (let page = 2; page <= first.total_pages; page += 1) {
    const next = await getProductBatchRows(batchId, {
      view,
      page,
      pageSize: 200,
      changedOnly,
    });
    items.push(...next.items);
  }
  return {
    ...first,
    items,
    page: 1,
    page_size: Math.max(items.length, 1),
    total_items: items.length,
    total_pages: 1,
  };
}

export async function commitProductBatch(
  batchId: number,
): Promise<CommitResult> {
  return parseResponse(
    await fetchWithAuth(
      `${API_BASE}/api/data-upload/workbench/batches/${batchId}/commit`,
      {
        method: "POST",
        timeout: 120000,
      },
    ),
  );
}

export async function cancelProductBatch(batchId: number): Promise<void> {
  await parseResponse(
    await fetchWithAuth(
      `${API_BASE}/api/data-upload/workbench/batches/${batchId}/cancel`,
      {
        method: "POST",
      },
    ),
  );
}

export async function listProductBatches(page = 1): Promise<WorkflowBatchPage> {
  return parseResponse(
    await fetchWithAuth(
      `${API_BASE}/api/data-upload/workbench/batches?page=${page}&page_size=10`,
    ),
  );
}

export async function getProductBatchOriginalUrl(
  batchId: number,
): Promise<string> {
  const result = await parseResponse<{ url: string }>(
    await fetchWithAuth(
      `${API_BASE}/api/data-upload/workbench/batches/${batchId}/original-url`,
    ),
  );
  return result.url;
}

export async function rollbackProductBatch(batchId: number): Promise<void> {
  const result = await parseResponse<{
    skipped: number;
    conflicts?: Array<{ reason: string }>;
  }>(
    await fetchWithAuth(
      `${API_BASE}/api/data-upload/workbench/batches/${batchId}/rollback`,
      { method: "POST", timeout: 120000 },
    ),
  );
  if (result.skipped > 0) {
    throw new Error(
      `回滚未完成：${result.conflicts?.map((item) => item.reason).join("；") || "存在后续修改或数据冲突，请人工核对"}`,
    );
  }
}
