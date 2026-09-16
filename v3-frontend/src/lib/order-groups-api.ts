/**
 * order-groups-api — TypeScript client for /api/order-groups (R7, 2026-06-16).
 *
 * Display-layer grouping for the order list page: each Order keeps its own
 * identity; the group is just a collapsible visual aggregation Felix asked
 * for ("三个订单是同一天一条船的，能合到一天去"). All mutations go through
 * `fetchWithAuth` for the same auto-refresh-401 behavior as orders-api.
 */

import { fetchWithAuth } from "./fetch-with-auth";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

export interface OrderGroup {
  id: number;
  user_id: number;
  name: string;
  ship_name: string | null;
  loading_date: string | null;
  order_count: number;
  can_manage: boolean;
  can_generate_inquiry: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface OrderGroupCreateBody {
  name: string;
  ship_name?: string | null;
  loading_date?: string | null;
  /** Optional initial set of orders to assign at creation time. */
  order_ids?: number[];
}

export interface OrderGroupUpdateBody {
  name?: string;
  ship_name?: string | null;
  loading_date?: string | null;
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  // 204 No Content has no body.
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

export async function listOrderGroups(): Promise<OrderGroup[]> {
  const res = await fetchWithAuth(`${API_BASE}/api/order-groups`);
  return handle<OrderGroup[]>(res);
}

export async function createOrderGroup(
  body: OrderGroupCreateBody,
): Promise<OrderGroup> {
  const res = await fetchWithAuth(`${API_BASE}/api/order-groups`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle<OrderGroup>(res);
}

export async function updateOrderGroup(
  id: number,
  body: OrderGroupUpdateBody,
): Promise<OrderGroup> {
  const res = await fetchWithAuth(`${API_BASE}/api/order-groups/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle<OrderGroup>(res);
}

export async function deleteOrderGroup(id: number): Promise<void> {
  const res = await fetchWithAuth(`${API_BASE}/api/order-groups/${id}`, {
    method: "DELETE",
  });
  return handle<void>(res);
}

export async function assignOrdersToGroup(
  groupId: number,
  orderIds: number[],
): Promise<OrderGroup> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/order-groups/${groupId}/orders`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ order_ids: orderIds }),
    },
  );
  return handle<OrderGroup>(res);
}

export async function removeOrderFromGroup(
  groupId: number,
  orderId: number,
): Promise<void> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/order-groups/${groupId}/orders/${orderId}`,
    { method: "DELETE" },
  );
  return handle<void>(res);
}

/**
 * G2 (R7 Gap 2, 2026-06-22): trigger a merged inquiry for every order
 * in the group. Returns `anchor_order_id` so the caller can navigate to
 * the standard per-order inquiry detail page / SSE stream.
 */
export async function generateInquiryForGroup(
  groupId: number,
): Promise<{ ok: true; group_id: number; anchor_order_id: number; inquiry_id: number; version: number; job_id: string; status: string }> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/order-groups/${groupId}/generate-inquiry`,
    { method: "POST" },
  );
  return handle<{ ok: true; group_id: number; anchor_order_id: number; inquiry_id: number; version: number; job_id: string; status: string }>(res);
}

export interface ArrangementInquirySupplier {
  supplier_id: number;
  supplier_name: string | null;
  product_count: number;
  status: string;
  error_message: string | null;
  template?: {
    id: number | null;
    name: string | null;
    method: string;
  } | null;
}

export interface ArrangementInquiryVersion {
  id: number;
  order_id: number;
  group_id: number;
  version: number;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  supplier_count: number;
  unassigned_count: number;
  member_snapshot: Array<{
    order_id?: number;
    po_number?: string | null;
    document_id?: number | null;
  }>;
  unmatched_items: Array<{
    source_po_number?: string | null;
    po_number?: string | null;
    source_order_id?: number;
    source_line_number?: number | null;
    source_line?: string | number | null;
    page?: string | number | null;
    original_name?: string | null;
    product_name?: string | null;
    match_reason?: string | null;
  }>;
  error_message: string | null;
  suppliers: ArrangementInquirySupplier[];
}

export async function listGroupInquiries(
  groupId: number,
): Promise<ArrangementInquiryVersion[]> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/order-groups/${groupId}/inquiries`,
  );
  return handle<ArrangementInquiryVersion[]>(res);
}

export interface AutoGroupResult {
  created_groups: number;
  assigned_orders: number;
  skipped: { order_id: number; reason: string }[];
}

export async function autoGroupOrders(): Promise<AutoGroupResult> {
  return handle<AutoGroupResult>(await fetchWithAuth(`${API_BASE}/api/order-groups/auto-group`, { method: "POST" }));
}

export interface ArrangementOrder {
  id: number;
  po_number: string | null;
  filename: string;
  document_id: number | null;
  product_count: number;
  ship: string | null;
  day: string | null;
  port: string | null;
  status: string;
  fulfillment_status: string;
  inquiry_status: string | null;
  requires_human_review?: boolean;
  anomaly_count?: number;
  reason: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  processed_at?: string | null;
  matched_count?: number;
  unmatched_count?: number;
}

export interface SupplyArrangement {
  id: number;
  name: string;
  ship: string;
  day: string | null;
  date_basis: string | null;
  port_id: number | null;
  port: string;
  manual: boolean;
  can_manage: boolean;
  can_generate_inquiry: boolean;
  inquiry_version?: number | null;
  inquiry_members_changed?: boolean;
  inquiry_member_diff?: {
    added_order_ids: number[];
    removed_order_ids: number[];
  } | null;
  orders: ArrangementOrder[];
}

export interface ArrangementsResult {
  arrangements: SupplyArrangement[];
  unclassified: ArrangementOrder[];
  total_orders: number;
}

export async function listArrangements(): Promise<ArrangementsResult> {
  return handle(await fetchWithAuth(`${API_BASE}/api/order-groups/arrangements`));
}

export interface ArrangementWorkspaceSupplier {
  supplier_id: number;
  supplier_name: string;
  product_count: number;
  source_order_count: number;
  source_po_numbers: string[];
  status: string;
  error_message: string | null;
  template_id: number | null;
  template_name: string | null;
  template_method: string;
}

export interface ArrangementWorkspace {
  arrangement: SupplyArrangement;
  summary: {
    product_count: number;
    matched_count: number;
    unmatched_count: number;
    supplier_count: number;
    anomaly_count: number;
    updated_at: string | null;
  };
  latest_inquiry: {
    id: number;
    version: number;
    status: string;
    started_at: string | null;
    completed_at: string | null;
    unassigned_count: number;
    error_message: string | null;
    inputs_changed: boolean;
  } | null;
  suppliers: ArrangementWorkspaceSupplier[];
  unassigned_items: Array<{
    source_order_id: number | null;
    source_po_number: string | null;
    source_line: string | number | null;
    product_name: string | null;
    reason: string;
  }>;
  activity: Array<{
    occurred_at: string;
    kind: "po_received" | "po_processed" | "inquiry" | string;
    message: string;
    order_id: number | null;
  }>;
}

export async function getArrangementWorkspace(
  groupId: number,
): Promise<ArrangementWorkspace> {
  return handle(
    await fetchWithAuth(`${API_BASE}/api/order-groups/arrangements/${groupId}`),
  );
}

export async function updateArrangementWorkspace(
  groupId: number,
  body: { ship_name: string; loading_date: string; port_id: number },
): Promise<ArrangementWorkspace> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/order-groups/arrangements/${groupId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  return handle<ArrangementWorkspace>(res);
}

export async function classifyOrder(orderId: number): Promise<{ group_id: number | null; reason: string | null }> {
  return handle(await fetchWithAuth(`${API_BASE}/api/order-groups/orders/${orderId}/classify`, { method: "POST" }));
}
