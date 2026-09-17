/**
 * Bulk image upload API client (2026-06-22).
 *
 * Three flows backing the `/dashboard/data/bulk-images` page:
 *   1. download template (filtered ZIP of empty product folders)
 *   2. upload filled ZIP → preview (matched / unmatched / error rows)
 *   3. commit batch → async ingestion; UI polls until completed/error
 *
 * Mirrors the v3-backend `apps/http/masterdata.py` endpoints. Auth
 * via fetchWithAuth.
 */

import { getToken } from "./auth";
import { refreshAccessToken } from "./api";
import { fetchWithAuth } from "./fetch-with-auth";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

// ─── Types ─────────────────────────────────────────────────────

export type BulkImageBatchStatus =
  | "uploading"
  | "validating"
  | "preview_ready"
  | "processing"
  | "completed"
  | "cancelled"
  | "error";

export type BulkImageRowStatus =
  | "matched"
  | "unmatched"
  | "error"
  | "checking"
  | "ready"
  | "needs_attention"
  | "excluded"
  | "committed"
  | "committed_failed";

export interface DirectImageExisting {
  id: number;
  filename: string;
  display_order: number;
  preview_url: string;
}

export interface DirectImagePlan {
  product_id: number;
  product_code: string | null;
  product_name: string | null;
  expected_existing_image_ids: number[];
  ordered_items: string[];
  existing_images: DirectImageExisting[];
  revision: number;
}

export interface BulkImageStagingRow {
  id: number;
  zip_path: string;
  country_name: string | null;
  port_name: string | null;
  product_code: string | null;
  image_filename: string;
  file_size_bytes: number;
  product_id: number | null;
  status: BulkImageRowStatus;
  error_message: string | null;
  issue_code?: string | null;
  upload_order?: number;
  decision?: "include" | "exclude";
  retry_count?: number;
  committed_image_id?: number | null;
  preview_url?: string | null;
}

export interface BulkImageBatch {
  id: number;
  zip_filename: string;
  status: BulkImageBatchStatus;
  total_files: number;
  matched_count: number;
  unmatched_count: number;
  error_count: number;
  ingested_count: number;
  error_message: string | null;
  source_type?: "zip" | "direct";
  excluded_count?: number;
  failed_count?: number;
  can_continue?: boolean;
  created_at: string | null;
  completed_at: string | null;
  rows: BulkImageStagingRow[];
  plans?: DirectImagePlan[];
}

// ─── Helpers ───────────────────────────────────────────────────

async function handleJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// ─── API ───────────────────────────────────────────────────────

/**
 * Build the URL for the template-download endpoint with the given
 * filters. Returns a string — the caller triggers the download via
 * window.location or an <a download> link to preserve the auth header
 * via cookies (fetchWithAuth + blob would lose the filename hint).
 *
 * Actually we DO want Authorization headers, so we fetch with auth
 * then convert to blob. See `downloadTemplate` below.
 */
export async function downloadTemplate(opts: {
  countryIds?: number[];
  portIds?: number[];
  onlyMissingImages?: boolean;
}): Promise<Blob> {
  const params = new URLSearchParams();
  for (const id of opts.countryIds ?? []) params.append("country_ids", String(id));
  for (const id of opts.portIds ?? []) params.append("port_ids", String(id));
  if (opts.onlyMissingImages) params.append("only_missing_images", "true");

  const res = await fetchWithAuth(
    `${API_BASE}/api/data/bulk-images/template?${params}`,
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "下载失败" }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.blob();
}

export async function uploadBulkImageZip(file: File): Promise<BulkImageBatch> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetchWithAuth(
    `${API_BASE}/api/data/bulk-images/preview`,
    { method: "POST", body: fd },
  );
  return handleJson<BulkImageBatch>(res);
}

/**
 * Upload variant that exposes progress. Uses XHR (not fetch) because
 * fetch API has no `upload.onprogress` — a 100MB ZIP on a slow uplink
 * is 5+ minutes and a silent spinner is unacceptable.
 *
 * Token refresh: fetch API's helper (`fetchWithAuth`) auto-refreshes
 * on 401 and retries. XHR can't share that logic, so we mirror it
 * manually: first try, on 401 refresh + retry once, on second 401 fail.
 */
export async function uploadBulkImageZipWithProgress(
  file: File,
  onProgress: (loaded: number, total: number) => void,
): Promise<BulkImageBatch> {
  const attempt = async (token: string | null) =>
    new Promise<BulkImageBatch>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_BASE}/api/data/bulk-images/preview`);
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded, e.total);
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText) as BulkImageBatch);
          } catch (err) {
            reject(err);
          }
        } else {
          // Attach status so the caller can distinguish 401 vs 4xx/5xx.
          const err = new Error(_parseXhrError(xhr)) as Error & {
            status?: number;
          };
          err.status = xhr.status;
          reject(err);
        }
      };
      xhr.onerror = () => reject(new Error("网络错误"));
      xhr.onabort = () => reject(new Error("上传已取消"));

      const fd = new FormData();
      fd.append("file", file);
      xhr.send(fd);
    });

  try {
    return await attempt(getToken());
  } catch (err) {
    const status = (err as Error & { status?: number }).status;
    if (status !== 401) throw err;
    // Try to refresh once and re-attempt.
    const refreshed = await refreshAccessToken();
    if (!refreshed) throw new Error("会话已过期，请重新登录");
    return await attempt(refreshed.access_token);
  }
}

function _parseXhrError(xhr: XMLHttpRequest): string {
  try {
    const body = JSON.parse(xhr.responseText);
    return body?.detail || `HTTP ${xhr.status}`;
  } catch {
    return `HTTP ${xhr.status}`;
  }
}

export async function getBulkImageBatch(batchId: number): Promise<BulkImageBatch> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/data/bulk-images/${batchId}`,
  );
  return handleJson<BulkImageBatch>(res);
}

export async function commitBulkImageBatch(
  batchId: number,
): Promise<BulkImageBatch> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/data/bulk-images/${batchId}/commit`,
    { method: "POST" },
  );
  return handleJson<BulkImageBatch>(res);
}

export async function cancelBulkImageBatch(
  batchId: number,
  opts?: { force?: boolean },
): Promise<void> {
  const q = opts?.force ? "?force=true" : "";
  const res = await fetchWithAuth(
    `${API_BASE}/api/data/bulk-images/${batchId}${q}`,
    { method: "DELETE" },
  );
  if (!res.ok && res.status !== 204) {
    const err = await res.json().catch(() => ({ detail: "取消失败" }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
}

export async function createDirectImageBatch(): Promise<BulkImageBatch> {
  const res = await fetchWithAuth(`${API_BASE}/api/data/bulk-images/direct`, {
    method: "POST",
  });
  return handleJson<BulkImageBatch>(res);
}

export async function getActiveDirectImageBatch(): Promise<BulkImageBatch | null> {
  const res = await fetchWithAuth(`${API_BASE}/api/data/bulk-images/active`);
  return handleJson<BulkImageBatch | null>(res);
}

export async function listDirectImageBatches(): Promise<{ items: BulkImageBatch[] }> {
  const res = await fetchWithAuth(`${API_BASE}/api/data/bulk-images?limit=20`);
  return handleJson<{ items: BulkImageBatch[] }>(res);
}

export async function uploadDirectImage(
  batchId: number,
  file: File,
  productId?: number,
): Promise<BulkImageStagingRow> {
  const body = new FormData();
  body.append("file", file);
  if (productId) body.append("product_id", String(productId));
  const res = await fetchWithAuth(
    `${API_BASE}/api/data/bulk-images/${batchId}/files`,
    { method: "POST", body },
  );
  return handleJson<BulkImageStagingRow>(res);
}

export async function validateDirectImageBatch(batchId: number): Promise<BulkImageBatch> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/data/bulk-images/${batchId}/validate`,
    { method: "POST" },
  );
  return handleJson<BulkImageBatch>(res);
}

export async function updateDirectImageRow(
  batchId: number,
  rowId: number,
  body: { product_id?: number; decision?: "include" | "exclude" },
): Promise<BulkImageBatch> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/data/bulk-images/${batchId}/rows/${rowId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  return handleJson<BulkImageBatch>(res);
}

export async function saveDirectImagePlan(
  batchId: number,
  productId: number,
  items: string[],
  expectedRevision: number,
): Promise<DirectImagePlan> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/data/bulk-images/${batchId}/plans/${productId}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items, expected_revision: expectedRevision }),
    },
  );
  return handleJson<DirectImagePlan>(res);
}

export async function replaceDirectImageRowFile(
  batchId: number,
  rowId: number,
  file: File,
): Promise<BulkImageBatch> {
  const body = new FormData();
  body.append("file", file);
  const res = await fetchWithAuth(
    `${API_BASE}/api/data/bulk-images/${batchId}/rows/${rowId}/file`,
    { method: "PUT", body },
  );
  return handleJson<BulkImageBatch>(res);
}

export async function resumeDirectImageBatch(batchId: number): Promise<BulkImageBatch> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/data/bulk-images/${batchId}/resume`,
    { method: "POST" },
  );
  return handleJson<BulkImageBatch>(res);
}

export async function retryDirectImageRow(
  batchId: number,
  rowId: number,
): Promise<BulkImageStagingRow> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/data/bulk-images/${batchId}/rows/${rowId}/retry`,
    { method: "POST" },
  );
  return handleJson<BulkImageStagingRow>(res);
}
