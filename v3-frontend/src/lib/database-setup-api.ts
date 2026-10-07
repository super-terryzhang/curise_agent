import { fetchWithAuth } from "./fetch-with-auth";

const BASE =
  (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001") +
  "/api/database-setup";

export interface SetupStatus {
  enabled: boolean;
  mode: "temporary";
  database_name: string;
  schema_version: number | null;
  field_count: number;
  product_count: number;
  price_period_count: number;
}

export interface ImportCounts {
  create: number;
  update: number;
  skip: number;
  warning: number;
  block: number;
}

export interface ImportBatch {
  id: string;
  filename: string;
  status: string;
  total_rows: number;
  counts: ImportCounts;
  can_commit: boolean;
  created_at?: string | null;
  validated_at?: string | null;
  committed_at?: string | null;
  rolled_back_at?: string | null;
}

export interface ImportIssue {
  severity: "warning" | "block";
  code: string;
  message: string;
  sheet?: string | null;
  row?: number | null;
  field?: string | null;
}

export interface ImportRow {
  id: string;
  sheet: "products" | "prices";
  row: number;
  action: string;
  product_code: string | null;
  port_id: number | null;
  before_values: Record<string, unknown> | null;
  normalized_values: Record<string, unknown>;
  issues: ImportIssue[];
}

export interface ImportRowsPage extends ImportBatch {
  page: number;
  page_size: number;
  total: number;
  pages: number;
  issues: ImportIssue[];
  items: ImportRow[];
}

export interface CommitResult {
  batch_id: string;
  status: "committed";
  created: number;
  updated: number;
  skipped: number;
}

export interface RollbackResult {
  batch_id: string;
  status: "rolled_back";
  restored: number;
  archived: number;
}

export interface ProductExtension {
  label: string;
  type: string;
  value: unknown;
}

export interface SetupProduct {
  id: number;
  code: string | null;
  name: string;
  port: string | null;
  country: string | null;
  supplier: string | null;
  unit: string | null;
  category: string | null;
  brand: string | null;
  status: boolean;
  revision: number;
  extensions: ProductExtension[];
}

export interface PricePeriod {
  id: number;
  product_id: number;
  price_type: "purchase" | "selling";
  amount: number;
  currency: string | null;
  effective_from: string;
  effective_to: string;
  status: boolean;
}

export interface SetupProductDetail extends SetupProduct {
  schema_version: number | null;
  price_periods: PricePeriod[];
}

export interface ProductPage {
  page: number;
  page_size: number;
  total: number;
  pages: number;
  items: SetupProduct[];
}

export class DatabaseSetupApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public uncertain = false,
  ) {
    super(message);
    this.name = "DatabaseSetupApiError";
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetchWithAuth(BASE + path, {
      ...options,
      timeout: 120_000,
    });
  } catch {
    throw new DatabaseSetupApiError(
      0,
      "NETWORK_ERROR",
      options?.method && options.method !== "GET"
        ? "操作结果暂时无法确认，请先核对批次状态"
        : "无法加载，请重试",
      Boolean(options?.method && options.method !== "GET"),
    );
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    throw new DatabaseSetupApiError(
      response.status,
      detail?.code || "REQUEST_ERROR",
      detail?.message || (typeof detail === "string" ? detail : "请求未完成"),
      response.status >= 500 && Boolean(options?.method && options.method !== "GET"),
    );
  }
  return response.json() as Promise<T>;
}

export const getDatabaseSetupStatus = () => request<SetupStatus>("/status");

export async function downloadProductTemplate(includeExisting = false): Promise<Blob> {
  const response = await fetchWithAuth(
    `${BASE}/product-template?include_existing=${includeExisting}`,
  );
  if (!response.ok)
    throw new DatabaseSetupApiError(response.status, "DOWNLOAD_FAILED", "模板下载失败");
  return response.blob();
}

export async function uploadDatabaseWorkbook(file: File): Promise<ImportBatch> {
  const form = new FormData();
  form.append("file", file);
  return request<ImportBatch>("/imports", { method: "POST", body: form });
}

export const validateDatabaseImport = (batchId: string) =>
  request<ImportBatch>(`/imports/${batchId}/validate`, { method: "POST" });

export const getDatabaseImportRows = (
  batchId: string,
  page = 1,
  pageSize = 50,
) =>
  request<ImportRowsPage>(
    `/imports/${batchId}/rows?page=${page}&page_size=${pageSize}`,
  );

export const commitDatabaseImport = (batchId: string) =>
  request<CommitResult>(`/imports/${batchId}/commit`, { method: "POST" });

export async function commitImportWithRecovery(batchId: string): Promise<CommitResult> {
  try {
    return await commitDatabaseImport(batchId);
  } catch (error) {
    const state = await getDatabaseImportRows(batchId, 1, 1);
    if (state.status === "committed") {
      return {
        batch_id: batchId,
        status: "committed",
        created: state.counts.create,
        updated: state.counts.update,
        skipped: state.counts.skip,
      };
    }
    throw error;
  }
}

export const rollbackDatabaseImport = (batchId: string) =>
  request<RollbackResult>(`/imports/${batchId}/rollback`, { method: "POST" });

export const listSetupProducts = (q = "", page = 1, pageSize = 50) => {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (q.trim()) params.set("q", q.trim());
  return request<ProductPage>(`/products?${params}`);
};

export const getSetupProduct = (productId: number) =>
  request<SetupProductDetail>(`/products/${productId}`);
