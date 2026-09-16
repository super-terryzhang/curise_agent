import { fetchWithAuth } from "./fetch-with-auth";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

export type DocumentStatus = "uploaded" | "extracting" | "extracted" | "error";

export interface DocumentSummary {
  id: number;
  user_id: number;
  // 2026-07-03: shared company-wide, so we surface uploader info.
  // Optional — old rows or deleted users leave it null; UI falls back
  // to `user_id`.
  uploader_email?: string | null;
  uploader_name?: string | null;
  filename: string;
  // Optional user-set rename (P3 2026-06-21). UI shows `display_name ?? filename`.
  display_name: string | null;
  // Optional folder this doc lives in (P3B 2026-06-21). null = root.
  folder_id: number | null;
  file_url: string | null;
  file_type: string;
  file_size_bytes: number | null;
  doc_type: string | null;
  extraction_method: string | null;
  status: DocumentStatus;
  processing_error: string | null;
  product_count: number;
  linked_order_id: number | null;
  preview_url: string | null;
  preview_text: string | null;
  // Tags + summary populated by the workflow's summarizer step (2026-05-08).
  // tags mixes system tags (`file_type:pdf`, `doc_type:purchase_order`,
  // `extractor:pypdfium2`, `has_products:true`, `lang:en`) with LLM topic tags
  // (`beef-supplier`, `celebrity-cruise`).
  tags: string[] | null;
  // 2026-05-10: user-managed tags (manually added in the UI). Lives
  // alongside `tags` so re-extracting doesn't wipe the user's labels.
  user_tags: string[] | null;
  summary: string | null;
  created_at: string | null;
  updated_at: string | null;
  extracted_at: string | null;
}

// `extracted_data` shape (mirrors v3_backend/domains/document/extraction/schema.py
// at schema_version 2.0). Domain-specific fields like metadata/products are
// populated by enrichment hooks (e.g. orders.enrichment) and are not part of
// the universal extraction contract.

export interface DocumentDetail extends DocumentSummary {
  content_markdown: string | null;
  extracted_data: {
    // Universal extraction (every extractor populates these)
    schema_version?: string;
    title?: string | null;
    language?: string | null;
    page_count?: number | null;
    markdown?: string | null;
    // Purchase-order enrichment (only on doc_type == "purchase_order")
    metadata?: Record<string, unknown>;
    order_metadata?: Record<string, unknown>;
    products?: Array<Record<string, unknown>>;
    field_evidence?: Record<string, unknown>;
    raw_extraction?: Record<string, unknown>;
    projection?: {
      purchase_order?: {
        confidence?: Record<string, unknown>;
      };
    };
  } | null;
}

export interface OrderPayload {
  document_id: number;
  doc_type: string | null;
  order_metadata: Record<string, unknown>;
  products: Array<Record<string, unknown>>;
  product_count: number;
  missing_fields: string[];
  blocking_missing_fields: string[];
  field_evidence: Record<string, unknown>;
  confidence_summary: Record<string, unknown>;
  ready_for_order_creation: boolean;
}

export interface PaginatedDocuments {
  total: number;
  items: DocumentSummary[];
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function uploadDocument(
  file: File,
  isPurchaseOrder: boolean = false,
): Promise<DocumentSummary> {
  const form = new FormData();
  form.append("file", file);
  form.append("is_purchase_order", String(isPurchaseOrder));
  const res = await fetchWithAuth(`${API_BASE}/api/documents/upload`, {
    method: "POST",
    body: form,
    timeout: 120000,
  } as RequestInit & { timeout?: number });
  return handleResponse<DocumentSummary>(res);
}

/**
 * Folder scope semantics (P3B 2026-06-21):
 *   undefined → no folder filter (legacy behavior, show everything)
 *   "root"    → only documents not in any folder
 *   <number>  → only documents in that folder
 */
export type FolderScope = number | "root" | undefined;

export async function listDocuments(params?: {
  status?: string;
  tags?: string[]; // AND filter — doc must contain all listed tags
  folder?: FolderScope;
  limit?: number;
  offset?: number;
}): Promise<PaginatedDocuments> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.tags && params.tags.length > 0) {
    for (const t of params.tags) qs.append("tag", t);
  }
  if (params?.folder !== undefined) {
    qs.set("folder_id", String(params.folder));
  }
  if (params?.limit) qs.set("limit", String(params.limit));
  if (params?.offset != null) qs.set("offset", String(params.offset));
  const query = qs.toString();
  const res = await fetchWithAuth(`${API_BASE}/api/documents${query ? `?${query}` : ""}`);
  return handleResponse<PaginatedDocuments>(res);
}

export async function getDocument(documentId: number): Promise<DocumentDetail> {
  const res = await fetchWithAuth(`${API_BASE}/api/documents/${documentId}`);
  return handleResponse<DocumentDetail>(res);
}

export async function getDocumentOrderPayload(documentId: number): Promise<OrderPayload> {
  const res = await fetchWithAuth(`${API_BASE}/api/documents/${documentId}/order-payload`);
  return handleResponse<OrderPayload>(res);
}

export interface DeleteDocumentResult {
  ok: boolean;
  document_id: number;
  unlinked_order_id: number | null;
}

export type SupportedDocType = "purchase_order" | "unknown";

export async function updateDocumentType(
  documentId: number,
  docType: SupportedDocType,
): Promise<DocumentDetail> {
  const res = await fetchWithAuth(`${API_BASE}/api/documents/${documentId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_type: docType }),
  });
  return handleResponse<DocumentDetail>(res);
}

/**
 * Re-run extraction on an already-uploaded document. Sets status to
 * "extracting" server-side and schedules the workflow; the detail
 * page's polling picks up the new terminal state.
 *
 * Used by the "重新提取" button shown after a failed run.
 */
export async function reextractDocument(
  documentId: number,
): Promise<DocumentDetail> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/documents/${documentId}/reextract`,
    { method: "POST" },
  );
  return handleResponse<DocumentDetail>(res);
}

// ─── User-managed tags (2026-05-10) ──────────────────────────────────────
//
// Lives alongside the auto-generated `tags` field. Add/remove via the UI;
// listUserTags powers the filter chips on the document list page.

export async function addUserTag(
  documentId: number,
  tag: string,
): Promise<DocumentDetail> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/documents/${documentId}/user-tags`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tag }),
    },
  );
  return handleResponse<DocumentDetail>(res);
}

export async function removeUserTag(
  documentId: number,
  tag: string,
): Promise<DocumentDetail> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/documents/${documentId}/user-tags/${encodeURIComponent(tag)}`,
    { method: "DELETE" },
  );
  return handleResponse<DocumentDetail>(res);
}

export interface UserTagSummary {
  name: string;
  count: number;
}

export async function listUserTags(): Promise<UserTagSummary[]> {
  const res = await fetchWithAuth(`${API_BASE}/api/documents/user-tags`);
  return handleResponse<UserTagSummary[]>(res);
}


export async function updateDocumentMetadata(
  documentId: number,
  fields: Record<string, string | number | null>,
): Promise<OrderPayload> {
  const res = await fetchWithAuth(`${API_BASE}/api/documents/${documentId}/metadata`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fields }),
  });
  return handleResponse<OrderPayload>(res);
}

// ─── Folders ───────────────────────────────────────────────────

export interface DocumentFolder {
  id: number;
  name: string;
  parent_folder_id: number | null;
  document_count: number;
  // 2026-07-22: user-picked color. Null → frontend falls back to the
  // deterministic palette in `folder-color.ts`.
  color: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export async function listFolders(): Promise<DocumentFolder[]> {
  const res = await fetchWithAuth(`${API_BASE}/api/document-folders`);
  return handleResponse<DocumentFolder[]>(res);
}

export async function createFolder(
  name: string,
  parentFolderId: number | null = null,
  color: string | null = null,
): Promise<DocumentFolder> {
  const res = await fetchWithAuth(`${API_BASE}/api/document-folders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name,
      parent_folder_id: parentFolderId,
      ...(color !== null ? { color } : {}),
    }),
  });
  return handleResponse<DocumentFolder>(res);
}

export async function updateFolder(
  folderId: number,
  patch: {
    name?: string;
    parent_folder_id?: number | null;
    color?: string | null;
  },
): Promise<DocumentFolder> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/document-folders/${folderId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    },
  );
  return handleResponse<DocumentFolder>(res);
}

export async function deleteFolder(folderId: number): Promise<void> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/document-folders/${folderId}`,
    { method: "DELETE" },
  );
  if (!res.ok && res.status !== 204) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `HTTP ${res.status}`);
  }
}

export async function moveDocumentToFolder(
  documentId: number,
  folderId: number | null,
): Promise<DocumentDetail> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/documents/${documentId}/folder`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_id: folderId }),
    },
  );
  return handleResponse<DocumentDetail>(res);
}


export async function renameDocument(
  documentId: number,
  displayName: string | null,
): Promise<DocumentDetail> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/documents/${documentId}/display-name`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: displayName }),
    },
  );
  return handleResponse<DocumentDetail>(res);
}


export async function createOrderFromDocument(
  documentId: number,
  force = false,
): Promise<{ id: number; status: string; product_count: number }> {
  const res = await fetchWithAuth(`${API_BASE}/api/documents/${documentId}/create-order`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force }),
  });
  return handleResponse<{ id: number; status: string; product_count: number }>(res);
}

export async function deleteDocument(
  documentId: number,
  force = false,
): Promise<DeleteDocumentResult> {
  const url = new URL(`${API_BASE}/api/documents/${documentId}`);
  if (force) url.searchParams.set("force", "true");
  const res = await fetchWithAuth(url.toString(), { method: "DELETE" });
  return handleResponse<DeleteDocumentResult>(res);
}
