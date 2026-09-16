import { fetchWithAuth } from "./fetch-with-auth";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

// ─── Types ─────────────────────────────────────────────────────

export interface ProductItem {
  id: number;
  revision: number;
  price_version: number;
  product_name_en: string | null;
  product_name_jp: string | null;
  code: string | null;
  unit: string | null;
  price: number | null;
  /**
   * R7 financial (migration 0014): our contracted selling price for cruise
   * customers. Distinct from `price` (default/list price, may drift). When
   * set, the matcher surfaces PO `unit_price` vs `contract_price` deltas in
   * the financial dashboard. Nullable — not every product is on contract.
   */
  contract_price: number | null;
  purchase_price_effective_from?: string | null;
  purchase_price_effective_to?: string | null;
  selling_price_effective_from?: string | null;
  selling_price_effective_to?: string | null;
  price_periods?: ProductPricePeriod[];
  /**
   * R5 (2026-06-22) list-page projection. `thumbnail_url` is a signed URL
   * to the primary (display_order=0) image — null when product has no
   * images. `image_count` powers the "+N" overlay on the table cell.
   */
  thumbnail_url: string | null;
  image_count: number;
  unit_size: string | null;
  pack_size: string | null;
  country_of_origin: string | null;
  brand: string | null;
  currency: string | null;
  status: boolean | null;
  country_name: string | null;
  category_name: string | null;
  supplier_name: string | null;
  port_name: string | null;
  country_id: number | null;
  category_id: number | null;
  supplier_id: number | null;
  port_id: number | null;
  effective_from: string | null;
  effective_to: string | null;
  /**
   * Computed availability: `status AND (effective_to is null OR
   * effective_to >= today)`. The UI's StatusBadge prefers this over
   * `status` so expired products show as 无效 even though `status` is
   * still True (semantics: `status` = manual switch, `is_effective` =
   * real-world usability). Optional for backward compat with pre-v43
   * backends that don't compute this server-side.
   */
  is_effective?: boolean | null;
}

export interface ProductPricePeriod {
  id: number;
  product_id: number;
  price_type: "purchase" | "selling";
  amount: number;
  currency: string | null;
  effective_from: string;
  effective_to: string;
  status: boolean;
  source: string;
  source_batch_id: number | null;
  created_by: number | null;
  updated_by: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface SupplierItem {
  id: number;
  name: string;
  contact: string | null;
  email: string | null;
  phone: string | null;
  // Inquiry letterhead fields — printed on the supplier-facing Excel via
  // the `supplier_address` / `supplier_fax` / ... template keys (see
  // domains/inquiry/field_inspection.SUPPLIER_FIELD_MAP). Editable
  // exclusively via /api/data/suppliers/{id} since 2026-05-28.
  address: string | null;
  zip_code: string | null;
  fax: string | null;
  default_payment_method: string | null;
  default_payment_terms: string | null;
  status: boolean | null;
  country_name: string | null;
  country_id: number | null;
  categories: string[];
  category_ids: number[];
}

// Shared writable subset used by both createSupplier and updateSupplier.
// `name` is required at create-time; everything else is optional/nullable.
// Defined here so the form components can also reference SupplierWritable
// to type their form state.
export interface SupplierWritable {
  name: string;
  country_id: number | null;
  contact: string | null;
  email: string | null;
  phone: string | null;
  address: string | null;
  zip_code: string | null;
  fax: string | null;
  default_payment_method: string | null;
  default_payment_terms: string | null;
  category_ids: number[];
  status: boolean;
}

export interface CountryItem {
  id: number;
  name: string;
  code: string | null;
  status: boolean | null;
}

export interface PortItem {
  id: number;
  name: string;
  code: string | null;
  location: string | null;
  status: boolean | null;
  country_name: string | null;
  country_id: number | null;
}

export interface CategoryItem {
  id: number;
  name: string;
  code: string | null;
  description: string | null;
  status: boolean | null;
}

// ─── Helpers ───────────────────────────────────────────────────

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetchWithAuth(`${API_BASE}${path}`, options);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

async function apiVoid(path: string, options?: RequestInit): Promise<void> {
  const res = await fetchWithAuth(`${API_BASE}${path}`, options);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
}

function jsonBody(data: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  };
}

function patchBody(data: unknown): RequestInit {
  return {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  };
}

// ─── Paginated Response ──────────────────────────────────────

export interface PaginatedResponse<T> {
  total: number;
  items: T[];
}

// ─── List API Functions ───────────────────────────────────────

export function listProducts(params?: {
  search?: string;
  country_id?: number;
  category_id?: number;
  supplier_id?: number;
  /**
   * v45+: server-side 状态过滤. True = only 有效 products (manual-on AND
   * (no expiry OR expiry not yet reached)); False = only 无效; omit/null
   * = all. Mirrors the StatusBadge logic so the filtered list agrees with
   * what each row's badge displays.
   */
  is_effective?: boolean;
  limit?: number;
  offset?: number;
}): Promise<PaginatedResponse<ProductItem>> {
  const qs = new URLSearchParams();
  if (params?.search) qs.set("search", params.search);
  if (params?.country_id) qs.set("country_id", String(params.country_id));
  if (params?.category_id) qs.set("category_id", String(params.category_id));
  if (params?.supplier_id) qs.set("supplier_id", String(params.supplier_id));
  if (params?.is_effective !== undefined && params?.is_effective !== null) {
    qs.set("is_effective", String(params.is_effective));
  }
  if (params?.limit) qs.set("limit", String(params.limit));
  if (params?.offset != null) qs.set("offset", String(params.offset));
  const query = qs.toString();
  return api<PaginatedResponse<ProductItem>>(`/api/data/products${query ? `?${query}` : ""}`);
}

export function listSuppliers() {
  return api<SupplierItem[]>("/api/data/suppliers");
}

export function listCountries() {
  return api<CountryItem[]>("/api/data/countries");
}

export function listPorts() {
  return api<PortItem[]>("/api/data/ports");
}

export function listCategories() {
  return api<CategoryItem[]>("/api/data/categories");
}

// ─── Country CRUD ─────────────────────────────────────────────

export function createCountry(data: { name: string; code?: string; status?: boolean }) {
  return api<CountryItem>("/api/data/countries", jsonBody(data));
}

export function updateCountry(id: number, data: Partial<{ name: string; code: string; status: boolean }>) {
  return api<CountryItem>(`/api/data/countries/${id}`, patchBody(data));
}

export function deleteCountry(id: number) {
  return apiVoid(`/api/data/countries/${id}`, { method: "DELETE" });
}

// ─── Category CRUD ────────────────────────────────────────────

export function createCategory(data: { name: string; code?: string; description?: string; status?: boolean }) {
  return api<CategoryItem>("/api/data/categories", jsonBody(data));
}

export function updateCategory(id: number, data: Partial<{ name: string; code: string; description: string; status: boolean }>) {
  return api<CategoryItem>(`/api/data/categories/${id}`, patchBody(data));
}

export function deleteCategory(id: number) {
  return apiVoid(`/api/data/categories/${id}`, { method: "DELETE" });
}

// ─── Port CRUD ────────────────────────────────────────────────

export function createPort(data: { name: string; code?: string; country_id?: number | null; location?: string; status?: boolean }) {
  return api<PortItem>("/api/data/ports", jsonBody(data));
}

export function updatePort(id: number, data: Partial<{ name: string; code: string; country_id: number | null; location: string; status: boolean }>) {
  return api<PortItem>(`/api/data/ports/${id}`, patchBody(data));
}

export function deletePort(id: number) {
  return apiVoid(`/api/data/ports/${id}`, { method: "DELETE" });
}

// ─── Supplier CRUD ────────────────────────────────────────────

export function createSupplier(data: Partial<SupplierWritable> & { name: string }) {
  return api<SupplierItem>("/api/data/suppliers", jsonBody(data));
}

export function updateSupplier(id: number, data: Partial<SupplierWritable>) {
  return api<SupplierItem>(`/api/data/suppliers/${id}`, patchBody(data));
}

export function deleteSupplier(id: number) {
  return apiVoid(`/api/data/suppliers/${id}`, { method: "DELETE" });
}

// ─── Product CRUD ─────────────────────────────────────────────

export interface ProductCreateData {
  product_name_en: string;
  product_name_jp?: string | null;
  code?: string | null;
  country_id?: number | null;
  category_id?: number | null;
  supplier_id?: number | null;
  port_id?: number | null;
  unit?: string | null;
  price?: number | null;
  // Contract-bound selling price (we own as the seller). Surfaces in
  // the FinancialTab as the baseline against PO unit_price. DB column
  // remains `contract_price` (migration 0014) but the UI label was
  // renamed to 「卖价」 in 2026-06-22 because "合同价" confused users
  // — they read it as the supplier's contract price, not ours.
  contract_price?: number | null;
  purchase_price_effective_from?: string | null;
  purchase_price_effective_to?: string | null;
  selling_price_effective_from?: string | null;
  selling_price_effective_to?: string | null;
  unit_size?: string | null;
  pack_size?: string | null;
  country_of_origin?: string | null;
  brand?: string | null;
  currency?: string | null;
  effective_from?: string | null;
  effective_to?: string | null;
  status?: boolean;
}

export function createProduct(data: ProductCreateData) {
  return api<ProductItem>("/api/data/products", jsonBody(data));
}

export function updateProduct(id: number, data: Partial<ProductCreateData> & { expected_revision: number }) {
  return api<ProductItem>(`/api/data/products/${id}`, patchBody(data));
}

export function deleteProduct(id: number, revision: number) {
  return apiVoid(`/api/data/products/${id}?expected_revision=${revision}`, { method: "DELETE" });
}

export function listProductPricePeriods(productId: number) {
  return api<ProductPricePeriod[]>(`/api/data/products/${productId}/price-periods`);
}

export function createProductPricePeriod(
  productId: number,
  data: {
    price_type: "purchase" | "selling";
    amount: number;
    currency?: string | null;
    effective_from: string;
    effective_to: string;
  },
) {
  return api<ProductPricePeriod>(
    `/api/data/products/${productId}/price-periods`,
    jsonBody(data),
  );
}

export function updateProductPricePeriod(
  productId: number,
  periodId: number,
  data: Partial<Pick<ProductPricePeriod, "amount" | "currency" | "effective_from" | "effective_to" | "status">>,
) {
  return api<ProductPricePeriod>(
    `/api/data/products/${productId}/price-periods/${periodId}`,
    patchBody(data),
  );
}

export function deactivateProductPricePeriod(productId: number, periodId: number) {
  return apiVoid(`/api/data/products/${productId}/price-periods/${periodId}`, {
    method: "DELETE",
  });
}

// ─── Exchange Rate CRUD ──────────────────────────────────────

export interface ExchangeRateItem {
  id: number;
  from_currency: string;
  to_currency: string;
  rate: number;
  effective_date: string;
  source: string;
  created_at?: string;
  updated_at?: string;
}

export function listExchangeRates(params?: {
  from_currency?: string;
  to_currency?: string;
}): Promise<ExchangeRateItem[]> {
  const qs = new URLSearchParams();
  if (params?.from_currency) qs.set("from_currency", params.from_currency);
  if (params?.to_currency) qs.set("to_currency", params.to_currency);
  const query = qs.toString();
  return api<ExchangeRateItem[]>(`/api/data/exchange-rates${query ? `?${query}` : ""}`);
}

export function createExchangeRate(data: {
  from_currency: string;
  to_currency: string;
  rate: number;
  effective_date: string;
}) {
  return api<ExchangeRateItem>("/api/data/exchange-rates", jsonBody(data));
}

export function updateExchangeRate(id: number, data: Partial<{ rate: number; effective_date: string }>) {
  return api<ExchangeRateItem>(`/api/data/exchange-rates/${id}`, patchBody(data));
}

export function deleteExchangeRate(id: number) {
  return apiVoid(`/api/data/exchange-rates/${id}`, { method: "DELETE" });
}

export function fetchExchangeRates(baseCurrency: string = "USD", targetCurrencies?: string[]) {
  return api<{ created: number; updated: number; base: string; date: string }>(
    "/api/data/exchange-rates/fetch",
    jsonBody({ base_currency: baseCurrency, target_currencies: targetCurrencies || [] }),
  );
}


// ─── Product Images (R5 2026-06-22) ───────────────────────────

/**
 * Outbound shape — mirrors `ProductImageResponse` on the backend.
 * The three `*_url` fields are signed; expire ~1 h after the API
 * returns. Treat them as point-in-time data, not durable IDs.
 */
export interface ProductImage {
  id: number;
  filename: string;
  file_type: string;
  file_size_bytes: number;
  display_order: number;
  alt_text: string | null;
  uploaded_at: string | null;
  thumbnail_url: string;
  medium_url: string;
  full_url: string;
}

export function listProductImages(productId: number) {
  return api<ProductImage[]>(`/api/data/products/${productId}/images`);
}

/**
 * Upload one image as multipart/form-data. Returns the freshly-created
 * ProductImage row, with display_order set to the next slot.
 *
 * Client-side checks done here avoid the server round-trip for
 * obviously-bad files (oversized, wrong MIME). These mirror the
 * server-side guards in `_product_images_service.add_product_image`.
 */
export async function uploadProductImage(
  productId: number,
  file: File,
): Promise<ProductImage> {
  // Cheap pre-flight: 5 MB cap matches the backend; fail fast so user
  // gets feedback before any upload bandwidth is spent.
  if (file.size > 5 * 1024 * 1024) {
    throw new Error(`图片过大（${Math.round(file.size / 1024)} KB），上限 5 MB`);
  }
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetchWithAuth(`${API_BASE}/api/data/products/${productId}/images`, {
    method: "POST",
    body: fd,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "上传失败" }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export function updateProductImage(
  productId: number,
  imageId: number,
  data: { alt_text?: string | null },
) {
  return api<ProductImage>(
    `/api/data/products/${productId}/images/${imageId}`,
    patchBody(data),
  );
}

export function reorderProductImages(
  productId: number,
  items: Array<{ id: number; display_order: number }>,
) {
  return api<ProductImage[]>(
    `/api/data/products/${productId}/images/reorder`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    },
  );
}

export function deleteProductImage(productId: number, imageId: number) {
  return apiVoid(
    `/api/data/products/${productId}/images/${imageId}`,
    { method: "DELETE" },
  );
}


export interface ProductPriceSnapshot {
  known_fields?: string[];
  legacy_recorded_at?: string | null;
  id: number;
  product_name_en?: string | null;
  code?: string | null;
  price: string | null;
  contract_price: string | null;
  purchase_price_effective_from?: string | null;
  purchase_price_effective_to?: string | null;
  selling_price_effective_from?: string | null;
  selling_price_effective_to?: string | null;
  currency?: string | null;
  unit?: string | null;
  unit_size?: string | null;
  pack_size?: string | null;
  supplier_id?: number | null;
  country_id?: number | null;
  port_id?: number | null;
}
export interface ProductPriceEvent {
  id: string;
  product_id: number;
  version: number | null;
  event_type: "initial" | "baseline" | "change" | "restore" | "delete" | "legacy";
  recorded_at: string;
  before: ProductPriceSnapshot | null;
  after: ProductPriceSnapshot | null;
  changed_fields: string[];
  actor_id: number | null;
  source: string;
  source_batch_id: number | null;
  restores_id: string | null;
}
export interface ProductPriceHistory {
  product_id: number;
  current: ProductPriceSnapshot | null;
  revision: number | null;
  deleted: boolean;
  total: number;
  change_count: number;
  max_version: number | null;
  items: ProductPriceEvent[];
  legacy_count: number;
  has_more: boolean;
}
export function getProductPriceHistory(id: number, params: {
  limit?: number; offset?: number; max_version?: number; field?: string;
  date_from?: string; date_to?: string; legacy?: boolean;
} = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  return api<ProductPriceHistory>(`/api/data/products/${id}/price-history?${query}`);
}
export function restoreProductPrice(id: number, eventId: string, revision: number) {
  return api<ProductItem>(`/api/data/products/${id}/price-history/restore`,
    jsonBody({ event_id: eventId, expected_revision: revision }));
}
