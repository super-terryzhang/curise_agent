import { getToken } from "./auth";
import { fetchWithAuth } from "./fetch-with-auth";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

// ─── Types ─────────────────────────────────────────────────────

export type OrderStatus =
  | "uploading"
  | "pending_template"
  | "extracting"
  | "extracted"
  | "matching"
  | "ready"
  | "error";

export interface OrderMetadata {
  po_number?: string;
  ship_name?: string;
  delivery_date?: string;
  loading_date?: string;
  order_date?: string;
  vendor_name?: string;
  currency?: string;
  destination_port?: string;
  [key: string]: unknown;
}

export interface MatchStatistics {
  total: number;
  matched: number;
  not_matched: number;
  match_rate: number;
}

export interface MatchResult {
  product_code: string;
  product_name: string;
  quantity: number | null;
  unit: string | null;
  unit_price: number | null;
  match_status: "matched" | "not_matched";
  match_score: number;
  match_reason: string;
  matched_product?: {
    id: number;
    code: string;
    product_name_en: string;
    product_name_jp: string | null;
    price: number | null;
    // Contract selling price — our side of record. Surfaced so the
    // FinancialTab can compare against this row's `unit_price` (cruise PO
    // price). R2 / Felix 2026-06-06.
    contract_price: number | null;
    currency: string | null;
    supplier_id: number | null;
    category_id: number | null;
    pack_size: string | null;
    unit: string | null;
  };
}

export interface OrderProduct {
  line_number?: number;
  product_code?: string;
  product_name: string;
  quantity: number | null;
  unit?: string;
  unit_price?: number | null;
  total_price?: number | null;
}

export interface AnomalyData {
  total_anomalies: number;
  schema_version?: number;
  anomaly_count?: number;
  warning_count?: number;
  error_count?: number;
  blocking_count?: number;
  requires_human_review?: boolean;
  pipeline?: Array<{
    step: number;
    name: string;
    status: string;
    message?: string | null;
    error_code?: string | null;
    suggestion?: string | null;
    evidence?: Record<string, unknown>;
    updated_at?: string;
  }>;
  findings?: Array<{
    code: string;
    rule_version?: number;
    step: number;
    severity: "warning" | "error" | "blocking";
    scope: "document" | "order" | "row" | "supplier" | string;
    category?: string;
    field?: string | null;
    row_index?: number | null;
    line_id?: string | null;
    source_line?: string | number | null;
    page?: string | number | null;
    product_code?: string | null;
    product_name?: string | null;
    message: string;
    suggestion?: string;
    evidence?: Record<string, unknown>;
  }>;
  price_anomalies: Array<{
    type: string;
    product_name: string;
    product_code: string;
    order_value: number;
    db_value: number;
    deviation: number;
    description: string;
  }>;
  quantity_anomalies: Array<{
    type: string;
    product_name: string;
    issue: string;
    value?: number;
    description: string;
  }>;
  completeness_issues: Array<string | Record<string, unknown>>;
}

export interface GeneratedFile {
  supplier_id: number;
  filename: string | null;
  file_url?: string;
  product_count: number;
  has_template?: boolean;
  error?: string;
  field_mapping?: Record<string, string> | null;
  template_id?: number | null;
  template_name?: string | null;
  selection_method?: "supplier" | "country" | "single" | "none";
  review_issues?: Array<{
    field: string;
    cell: string;
    issue: string;
    suggestion: string;
  }> | null;
}

export interface VerifyResult {
  cell: string;
  annotation?: string;
  value: string;
  status: "pass" | "fail" | "unchecked";
  reason?: string;
  suggestion?: string;
}

export interface SupplierInquiryData {
  status: "pending" | "generating" | "completed" | "error";
  supplier_name?: string;
  product_count?: number;
  subtotal?: number;
  currency?: string;
  missing_fields?: string[] | null;
  file?: {
    filename: string;
    file_url: string;
    preview_url?: string;
    product_count: number;
    has_template?: boolean;
    template_name?: string | null;
    template_id?: number | null;
    selection_method?: string;
    supplier_id?: number;
  };
  template?: {
    id?: number | null;
    name?: string | null;
    method?: string;
    selection_method?: string;
    count?: number;
  };
  verify_results?: VerifyResult[];
  elapsed_seconds?: number;
  error?: string;
}

export interface InquiryData {
  status?: string;
  supplier_count: number;
  total_products?: number;
  suppliers?: Record<string, SupplierInquiryData>;
  total_elapsed_seconds?: number;
  // Legacy fields
  generated_files?: GeneratedFile[];
  unassigned_count?: number;
  agent_summary?: string;
  agent_elapsed_seconds?: number;
  agent_steps?: number;
}

// ─── v3 P&L (cost-items + auto FX) ──────────────────────────
// Replaces the legacy `FinancialData`/`runFinancialAnalysis()` flow
// (deleted 2026-05-27). The `Order.financial_data` blob is still
// surfaced as raw JSON when historical orders are loaded — the new
// /financials endpoint computes fresh on every read.

export interface OrderCostItem {
  id: number | null;
  category: string;
  amount_original: number;
  currency_original: string;
  amount_display: number;
  notes: string | null;
  fx_ok: boolean;
}

export interface OrderProductPnlLine {
  product_code: string | null;
  product_name: string;
  quantity: number;
  unit_price: number;
  revenue: number;
  unit_cost: number | null;
  cost: number;
  profit: number;
  margin: number;
  supplier_id: number | null;
  matched: boolean;
  // contract_price — our side's contracted selling price (Product.contract_price),
  // converted into display currency. Surfaced so the UI can flag PO typos
  // / contract drift: compare to `unit_price`. R2 / Felix 2026-06-06.
  contract_price: number | null;
}

export interface OrderFinancials {
  display_currency: string;
  order_currency: string;
  tax_rate: number;
  summary: {
    product_revenue: number;
    product_cost: number;
    extra_costs_total: number;
    total_cost: number;
    gross_profit: number;
    gross_margin: number;
    tax_amount: number;
    net_profit: number;
    net_margin: number;
  };
  product_lines: OrderProductPnlLine[];
  cost_items: OrderCostItem[];
  warnings: string[];
  meta: {
    order_id: number;
    po_number: string | null;
    ship_name: string | null;
    status: string;
    delivery_date: string | null;
  };
}

export interface CostItemCreateBody {
  category: string;
  amount: number;
  currency: string;
  notes?: string | null;
}

export interface CostItemUpdateBody {
  category?: string;
  amount?: number;
  currency?: string;
  notes?: string | null;
}

export interface FinancialSettingsBody {
  display_currency?: string;
  tax_rate?: number;
}

export interface DeliveryItem {
  product_name: string;
  product_code?: string;
  ordered_qty: number;
  accepted_qty: number;
  rejected_qty: number;
  rejection_reason?: string;
  notes?: string;
}

export interface DeliveryData {
  delivered_at?: string;
  received_by?: string;
  items: DeliveryItem[];
  total_accepted: number;
  total_rejected: number;
  summary: string;
}

export interface OrderAttachment {
  filename: string;
  original_name: string;
  uploaded_at: string;
  description?: string;
}

export type FulfillmentStatus =
  | "pending"
  | "inquiry_sent"
  | "quoted"
  | "confirmed"
  | "delivering"
  | "delivered"
  | "invoiced"
  | "paid";

export interface TideEntry {
  time: string;
  type: "HIGH" | "LOW";
  height_m: number;
}

export interface WaveEntry {
  time: string;
  wave_height_m: number;
}

export interface MarineData {
  max_wave_height_m: number | null;
  max_wave_period_s: number | null;
  hourly_waves: WaveEntry[];
}

export interface DeliveryWeather {
  condition: string;
  temp_c: number | null;
  max_temp_c: number | null;
  min_temp_c: number | null;
  max_wind_kph: number | null;
  max_wind_gusts_kph?: number | null;
  total_precip_mm: number | null;
  avg_vis_km: number | null;
  avg_humidity: number | null;
  uv: number | null;
}

export interface DeliveryEnvironment {
  location: string;
  date: string;
  coordinates?: { lat: number; lon: number };
  tides: TideEntry[];
  weather: DeliveryWeather;
  marine?: MarineData;
  ai_summary: string;
  forecast_available?: boolean;
  days_until_available?: number;
  fetched_at: string;
  source: string;
}

export interface Order {
  id: number;
  user_id?: number;
  document_id?: number | null;
  filename: string;
  file_url: string | null;
  file_type: string;
  status: OrderStatus;
  processing_error: string | null;
  country_id: number | null;
  country_name: string | null;
  port_id: number | null;
  delivery_date: string | null;
  // loading_date — when supplies are loaded onto the cruise ship. Distinct
  // from delivery_date (when supplier ships to port). Added 2026-06-16
  // per Felix R6 — surfaces on the order list page.
  loading_date: string | null;
  // group_id — R7. When non-null, this order belongs to an OrderGroup and
  // is rendered inside OrderGroupsSection rather than the flat table.
  group_id: number | null;
  extraction_data: Record<string, unknown> | null;
  order_metadata: OrderMetadata | null;
  products: OrderProduct[] | null;
  product_count: number;
  total_amount: number | null;
  match_results: MatchResult[] | null;
  match_statistics: MatchStatistics | null;
  anomaly_data: AnomalyData | null;
  // Legacy JSON blob from the deleted /financial-analysis endpoint.
  // Surfaced for completeness on historical orders; new code reads
  // /financials instead. See `getOrderFinancials()`.
  financial_data: Record<string, unknown> | null;
  inquiry_data: InquiryData | null;
  has_inquiry: boolean;
  is_reviewed: boolean;
  reviewed_at: string | null;
  reviewed_by: number | null;
  review_notes: string | null;
  fulfillment_status: FulfillmentStatus;
  delivery_data: DeliveryData | null;
  delivery_environment: DeliveryEnvironment | null;
  invoice_number: string | null;
  invoice_amount: number | null;
  invoice_date: string | null;
  payment_amount: number | null;
  payment_date: string | null;
  payment_reference: string | null;
  attachments: OrderAttachment[];
  fulfillment_notes: string | null;
  template_id: number | null;
  template_match_method: string | null;
  created_at: string;
  updated_at: string;
  processed_at: string | null;
}

// List item is the same shape, just without large fields (backend handles this)
export type OrderListItem = Order;

// ─── Helpers ───────────────────────────────────────────────────

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// ─── Orders API ──────────────────────────────────────────────

export async function uploadOrder(file: File): Promise<Order> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetchWithAuth(`${API_BASE}/api/orders/upload`, {
    method: "POST",
    body: form,
    timeout: 120000, // 2 min — large PDF uploads can be slow
  } as RequestInit & { timeout?: number });
  return handleResponse<Order>(res);
}

export interface PaginatedOrders {
  total: number;
  items: OrderListItem[];
}

export async function listOrders(params?: {
  status?: string;
  search?: string;
  limit?: number;
  offset?: number;
}): Promise<PaginatedOrders> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.search) qs.set("search", params.search);
  if (params?.limit) qs.set("limit", String(params.limit));
  if (params?.offset != null) qs.set("offset", String(params.offset));

  const res = await fetchWithAuth(`${API_BASE}/api/orders?${qs.toString()}`, {
    headers: { "Content-Type": "application/json" },
  });
  return handleResponse<PaginatedOrders>(res);
}

export async function getOrder(orderId: number): Promise<Order> {
  const res = await fetchWithAuth(`${API_BASE}/api/orders/${orderId}`, {
    headers: { "Content-Type": "application/json" },
  });
  return handleResponse<Order>(res);
}

export async function deleteOrder(orderId: number): Promise<void> {
  const res = await fetchWithAuth(`${API_BASE}/api/orders/${orderId}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "删除失败" }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
}

export async function reviewOrder(
  orderId: number,
  notes?: string
): Promise<{ detail: string; reviewed_at: string }> {
  const res = await fetchWithAuth(`${API_BASE}/api/orders/${orderId}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ notes: notes || null }),
  });
  return handleResponse(res);
}

export interface PortItem {
  id: number;
  name: string;
  code: string | null;
  country_id: number | null;
  country_name: string | null;
  location: string | null;
  status: boolean | null;
}

export interface CountryItem {
  id: number;
  name: string;
  code: string | null;
  status: boolean | null;
}

export async function getPortsList(): Promise<PortItem[]> {
  const res = await fetchWithAuth(`${API_BASE}/api/data/ports`);
  if (!res.ok) return [];
  const data = await res.json();
  return Array.isArray(data) ? data : [];
}

export async function getCountriesList(): Promise<CountryItem[]> {
  const res = await fetchWithAuth(`${API_BASE}/api/data/countries`);
  if (!res.ok) return [];
  const data = await res.json();
  return Array.isArray(data) ? data : [];
}

export async function updateOrder(
  orderId: number,
  payload: {
    order_metadata?: Record<string, unknown>;
    products?: OrderProduct[];
    po_number?: string | null;
    ship_name?: string | null;
    vendor_name?: string | null;
    order_date?: string | null;
    currency?: string | null;
    port_id?: number | null;
    country_id?: number | null;
    // R6 — top-level date columns. The backend's OrderUpdateRequest
    // accepts them at the top level; mirror lands on the real columns
    // instead of getting buried in the order_metadata JSON.
    delivery_date?: string | null;
    loading_date?: string | null;
  }
): Promise<Order> {
  const res = await fetchWithAuth(`${API_BASE}/api/orders/${orderId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return handleResponse<Order>(res);
}

export async function rematchOrder(orderId: number): Promise<Order> {
  const res = await fetchWithAuth(`${API_BASE}/api/orders/${orderId}/rematch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // OrderRematchRequest fields are all optional — but FastAPI still
    // requires a body to be present. Empty `{}` satisfies the parser.
    body: "{}",
  });
  return handleResponse<Order>(res);
}

export async function reprocessOrder(orderId: number): Promise<Order> {
  const res = await fetchWithAuth(`${API_BASE}/api/orders/${orderId}/reprocess`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  return handleResponse<Order>(res);
}

export async function setOrderTemplate(
  orderId: number,
  templateId: number,
): Promise<Order> {
  const res = await fetchWithAuth(`${API_BASE}/api/orders/${orderId}/set-template`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ template_id: templateId }),
  });
  return handleResponse<Order>(res);
}

export async function runAnomalyCheck(orderId: number): Promise<Order> {
  const res = await fetchWithAuth(`${API_BASE}/api/orders/${orderId}/anomaly-check`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  return handleResponse<Order>(res);
}

// ─── v3 financials (cost items + auto FX + per-order settings) ──
// Replaces the legacy `runFinancialAnalysis()` POST. Financials are
// now computed fresh on every GET — no Order.financial_data write.

export async function getOrderFinancials(
  orderId: number,
): Promise<OrderFinancials> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/orders/${orderId}/financials`,
  );
  return handleResponse<OrderFinancials>(res);
}

export async function createOrderCostItem(
  orderId: number,
  body: CostItemCreateBody,
) {
  const res = await fetchWithAuth(
    `${API_BASE}/api/orders/${orderId}/cost-items`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  return handleResponse<{
    id: number;
    category: string;
    amount: number;
    currency: string;
    notes: string | null;
  }>(res);
}

export async function updateOrderCostItem(
  orderId: number,
  itemId: number,
  body: CostItemUpdateBody,
) {
  const res = await fetchWithAuth(
    `${API_BASE}/api/orders/${orderId}/cost-items/${itemId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  return handleResponse<{
    id: number;
    category: string;
    amount: number;
    currency: string;
    notes: string | null;
  }>(res);
}

export async function deleteOrderCostItem(
  orderId: number,
  itemId: number,
): Promise<void> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/orders/${orderId}/cost-items/${itemId}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "删除失败" }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
}

export async function updateOrderFinancialSettings(
  orderId: number,
  body: FinancialSettingsBody,
): Promise<OrderFinancials> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/orders/${orderId}/financial-settings`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  return handleResponse<OrderFinancials>(res);
}

export function orderFinancialsXlsxUrl(orderId: number): string {
  return `${API_BASE}/api/orders/${orderId}/financials/export.xlsx`;
}

export async function downloadOrderFinancialsXlsx(
  orderId: number,
): Promise<void> {
  // Use fetchWithAuth so the JWT lands in the Authorization header,
  // then trigger a browser download from the blob. Direct <a href>
  // can't carry the token across origins.
  const res = await fetchWithAuth(orderFinancialsXlsxUrl(orderId));
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "下载失败" }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  // Filename comes from Content-Disposition; we fall back to a sane default.
  const cd = res.headers.get("Content-Disposition") || "";
  const m = cd.match(/filename="([^"]+)"/);
  a.download = m ? m[1] : `order_${orderId}_financials.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function fetchDeliveryEnvironment(orderId: number): Promise<Order> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/orders/${orderId}/delivery-environment`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    }
  );
  return handleResponse<Order>(res);
}

export async function generateInquiry(orderId: number): Promise<Order> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/orders/${orderId}/generate-inquiry`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    }
  );
  return handleResponse<Order>(res);
}

// ─── Streaming Inquiry API ──────────────────────────────────

export interface InquiryStep {
  type: "tool_call" | "tool_result" | "thinking" | "preview" | "supplier_start" | "supplier_done" | "cancelled";
  tool_name?: string;
  tool_label?: string;
  content?: string;
  step_index?: number;
  elapsed_seconds?: number;
  duration_ms?: number;
  // Supplier/preview-specific
  supplier_id?: number;
  supplier_name?: string;
  product_count?: number;
  status?: string;
  html?: string;
}

export async function startGenerateInquiry(
  orderId: number,
  templateOverrides?: Record<number, number | null>,
  supplierIds?: number[],
): Promise<{ status: string; stream_key: string }> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/orders/${orderId}/generate-inquiry`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template_overrides: templateOverrides, supplier_ids: supplierIds }),
    }
  );
  return handleResponse<{ status: string; stream_key: string }>(res);
}

export async function cancelGenerateInquiry(
  orderId: number,
  streamKey?: string,
): Promise<{ status: string; stream_key: string }> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/orders/${orderId}/cancel-inquiry`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stream_key: streamKey }),
    }
  );
  return handleResponse<{ status: string; stream_key: string }>(res);
}

export function streamInquiryProgress(
  orderId: number,
  onStep: (step: InquiryStep) => void,
  onDone: (data: InquiryData | null) => void,
  onError: (err: Error) => void
): () => void {
  const controller = new AbortController();
  const token = getToken();

  (async () => {
    try {
      const res = await fetch(
        `${API_BASE}/api/orders/${orderId}/inquiry-stream`,
        {
          headers: { Authorization: `Bearer ${token}` },
          signal: controller.signal,
        }
      );

      if (!res.ok || !res.body) {
        onError(new Error(`HTTP ${res.status}`));
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          // Stream closed without an in-band terminal we recognised — the
          // backend always emits run_completed/run_error/run_cancelled
          // *before* closing, but if we somehow miss the dispatch, fall
          // back to onDone so the UI doesn't spin forever.
          onDone(null);
          return;
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const jsonStr = line.slice(6).trim();
          if (!jsonStr) continue;

          try {
            const event = JSON.parse(jsonStr);
            // Terminal event names: backend emits `run_completed`,
            // `run_error`, `run_cancelled`. Older code paths still use
            // bare `done`/`error`/`cancelled` — accept both for safety.
            // `run_idle` is what /inquiry-stream returns when there's no
            // active run (treat as "nothing to wait for, done").
            if (
              event.type === "done" ||
              event.type === "run_completed" ||
              event.type === "run_idle"
            ) {
              onDone(event.data || {});
              return;
            } else if (
              event.type === "cancelled" ||
              event.type === "run_cancelled" ||
              event.type === "run_replaced"
            ) {
              onError(new Error(event.message || "询价生成已停止"));
              return;
            } else if (event.type === "error" || event.type === "run_error") {
              onError(new Error(event.error || event.message || "生成失败"));
              return;
            } else {
              onStep(event as InquiryStep);
            }
          } catch {
            // skip malformed JSON
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        onError(err instanceof Error ? err : new Error("Stream failed"));
      }
    }
  })();

  return () => controller.abort();
}

export async function getOrderFiles(
  orderId: number
): Promise<GeneratedFile[]> {
  const res = await fetchWithAuth(`${API_BASE}/api/orders/${orderId}/files`, {
    headers: { "Content-Type": "application/json" },
  });
  return handleResponse<GeneratedFile[]>(res);
}

export async function getOrderFilePreview(
  orderId: number
): Promise<{ url: string; file_type: string; filename: string }> {
  const res = await fetchWithAuth(`${API_BASE}/api/orders/${orderId}/file-preview`);
  return handleResponse<{ url: string; file_type: string; filename: string }>(res);
}

export async function downloadOrderFile(
  orderId: number,
  filename: string
): Promise<void> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/orders/${orderId}/files/${filename}/download`,
    { method: "POST" },
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "下载失败" }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  const blob = await res.blob();
  triggerBlobDownload(blob, filename);
}

export async function downloadInquiryZip(orderId: number): Promise<void> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/orders/${orderId}/inquiry-files.zip`,
    { method: "GET", timeout: 120000 },
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "ZIP 下载失败" }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const m = /filename="?([^";]+)"?/.exec(cd);
  const filename = m?.[1] || `order_${orderId}_inquiries.zip`;
  triggerBlobDownload(blob, filename);
}

// Common blob-download helper. Defers URL.revokeObjectURL via setTimeout
// because revoking immediately after a.click() races the browser's download
// pipeline — FileSaver.js / Chromium bug 827932 / Firefox bug 1282407 all
// document this. 10s is plenty for the browser to commit the download.
function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

/** @deprecated Use downloadOrderFile() instead — this exposes token in URL */
export function getOrderFileDownloadUrl(
  orderId: number,
  filename: string
): string {
  const token = getToken();
  return `${API_BASE}/api/orders/${orderId}/files/${filename}?token=${token}`;
}

// ─── Inquiry Field Inspection ─────────────────────────────

export type InquiryFieldSource =
  | "metadata"
  | "port_master"
  | "supplier_master"
  | "empty";

export interface InquiryField {
  key: string;
  label: string;
  position: string;
  value: string | null;
  source: InquiryFieldSource;
  description: string | null;
}

export interface InquiryFieldsResponse {
  fields: InquiryField[];
  template: { id: number; name: string } | null;
  reason: string | null;
}

export async function getInquiryFields(
  orderId: number,
  supplierId: number,
  templateId?: number | null,
): Promise<InquiryFieldsResponse> {
  const url = new URL(
    `${API_BASE}/api/orders/${orderId}/inquiry-fields/${supplierId}`,
  );
  if (templateId != null) {
    url.searchParams.set("template_id", String(templateId));
  }
  const res = await fetchWithAuth(url.toString());
  return handleResponse<InquiryFieldsResponse>(res);
}

export async function patchInquiryFields(
  orderId: number,
  supplierId: number,
  edits: Record<string, string>,
): Promise<{
  ok: boolean;
  saved_order_keys: string[];
  saved_supplier_keys: string[];
}> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/orders/${orderId}/inquiry-fields/${supplierId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(edits),
    },
  );
  return handleResponse<{
    ok: boolean;
    saved_order_keys: string[];
    saved_supplier_keys: string[];
  }>(res);
}

// ─── Single Supplier Inquiry API ──────────────────────────

export async function startGenerateInquirySingleSupplier(
  orderId: number,
  supplierId: number,
  templateId?: number
): Promise<{ status: string; stream_key: string; supplier_id: number }> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/orders/${orderId}/generate-inquiry/${supplierId}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template_id: templateId }),
    }
  );
  return handleResponse<{ status: string; stream_key: string; supplier_id: number }>(res);
}

/** Stream inquiry progress with a custom stream_key (for single-supplier redo). */
export function streamInquiryProgressWithKey(
  orderId: number,
  streamKey: string,
  onStep: (step: InquiryStep) => void,
  onDone: (data: unknown) => void,
  onError: (err: Error) => void
): () => void {
  const controller = new AbortController();
  const token = getToken();

  (async () => {
    try {
      const res = await fetch(
        `${API_BASE}/api/orders/${orderId}/inquiry-stream?stream_key=${encodeURIComponent(streamKey)}`,
        {
          headers: { Authorization: `Bearer ${token}` },
          signal: controller.signal,
        }
      );

      if (!res.ok || !res.body) {
        onError(new Error(`HTTP ${res.status}`));
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          onDone(null);
          return;
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const jsonStr = line.slice(6).trim();
          if (!jsonStr) continue;

          try {
            const event = JSON.parse(jsonStr);
            if (
              event.type === "done" ||
              event.type === "run_completed" ||
              event.type === "run_idle"
            ) {
              onDone(event.data || {});
              return;
            } else if (
              event.type === "cancelled" ||
              event.type === "run_cancelled" ||
              event.type === "run_replaced"
            ) {
              onError(new Error(event.message || "询价生成已停止"));
              return;
            } else if (event.type === "error" || event.type === "run_error") {
              onError(new Error(event.error || event.message || "生成失败"));
              return;
            } else {
              onStep(event as InquiryStep);
            }
          } catch {
            // skip malformed JSON
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        onError(err instanceof Error ? err : new Error("Stream failed"));
      }
    }
  })();

  return () => controller.abort();
}

// ─── Inquiry Readiness ────────────────────────────────────

export interface FieldGap {
  key: string;
  cell: string;
  label: string;
  type: "text" | "date" | "number";
  category: "order" | "supplier" | "company" | "delivery";
  severity: "blocking" | "warning";
  current_value: string | null;
}

export interface FieldItem {
  key: string;
  cell: string;
  label: string;
  type: "text" | "date" | "number";
  category: "order" | "supplier" | "company" | "delivery";
  status: "resolved" | "overridden" | "missing";
  value: string | null;
  severity: "blocking" | "warning" | null;
}

/** One per-field row of the supplier letterhead — returned by the readiness
 * endpoint for every supplier, regardless of whether the bound template
 * actually maps that field to a cell. The inquiry card uses this to show
 * the user which of the 9 letterhead values are filled in on the supplier
 * master record (NOT order-overrides — those still live in `fields`).
 *
 * `column` is the column name on the `suppliers` DB table, used by the
 * frontend as the PATCH /api/data/suppliers/{id} payload key.
 */
export interface SupplierLetterheadField {
  key: string;        // template field key, e.g. "supplier_address"
  column: string;     // suppliers table column, e.g. "address"
  label: string;      // localized display label
  value: string | null;
  filled: boolean;
}

export interface SupplierReadiness {
  status: "ready" | "needs_input" | "completed" | "blocked";
  gen_status: "pending" | "generating" | "completed" | "error";
  supplier_name: string;
  product_count: number;
  subtotal: number;
  currency: string;
  template: {
    id: number | null;
    name: string | null;
    method: string;
    has_zone_config: boolean;
    candidate_count?: number;
  };
  fields: FieldItem[];
  gaps: FieldGap[];
  gap_summary: {
    total: number;
    resolved: number;
    warnings: number;
    blocking: number;
  };
  /** All 9 supplier letterhead fields with filled/empty status. Always
   * length 9 in the new contract; older backends (pre-2026-05-28) may
   * omit this — treat undefined as "old backend, no letterhead status". */
  supplier_letterhead?: SupplierLetterheadField[];
  file?: {
    filename: string;
    file_url: string;
    preview_url?: string;
    product_count: number;
  } | null;
  verify_results?: VerifyResult[];
  elapsed_seconds?: number;
  error?: string;
}

export interface InquiryReadiness {
  suppliers: Record<string, SupplierReadiness>;
  summary: {
    ready: number;
    needs_input: number;
    blocked: number;
    total: number;
  };
}

export async function getInquiryReadiness(
  orderId: number,
  templateOverrides?: Record<number, number | null>,
): Promise<InquiryReadiness> {
  let url = `${API_BASE}/api/orders/${orderId}/inquiry-readiness`;
  if (templateOverrides && Object.keys(templateOverrides).length > 0) {
    const filtered = Object.fromEntries(
      Object.entries(templateOverrides).filter(([, v]) => v !== null)
    );
    if (Object.keys(filtered).length > 0) {
      url += `?template_overrides=${encodeURIComponent(JSON.stringify(filtered))}`;
    }
  }
  const res = await fetchWithAuth(url);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "加载失败" }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// ─── Inquiry Data Preview ──────────────────────────────────

export interface InquiryDataPreviewField {
  cell: string;
  path: string;
  label: string;
  value: string | null;
  source: "order" | "supplier" | "company" | "delivery";
}

export interface InquiryDataPreview {
  supplier_id: number;
  supplier_name: string;
  template: {
    id: number | null;
    name: string | null;
    method: string;
    has_zone_config: boolean;
  };
  header_fields: InquiryDataPreviewField[];
  field_overrides: Record<string, string>;
  product_columns: [string, string][] | null;
  formula_columns: string[] | null;
  summary_formulas: { cell: string; type: string; label: string }[] | null;
  products: Record<string, unknown>[];
  total_products: number;
  warnings: string[];
  order_metadata: {
    po_number: string;
    ship_name: string;
    delivery_date: string;
    currency: string;
  };
}

export async function getInquiryDataPreview(
  orderId: number,
  supplierId: number,
  templateId?: number | null,
): Promise<InquiryDataPreview> {
  const params = templateId ? `?template_id=${templateId}` : "";
  const res = await fetchWithAuth(
    `${API_BASE}/api/orders/${orderId}/inquiry-data-preview/${supplierId}${params}`
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "预览失败" }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function saveInquiryFieldOverrides(
  orderId: number,
  supplierId: number,
  overrides: Record<string, string>,
): Promise<void> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/orders/${orderId}/inquiry-field-overrides/${supplierId}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ overrides }),
    },
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "保存失败" }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
}

export async function getInquiryPreview(
  orderId: number,
  supplierId: number
): Promise<string> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/orders/${orderId}/inquiry-preview/${supplierId}`
  );
  if (!res.ok) {
    throw new Error("预览加载失败");
  }
  return res.text();
}
