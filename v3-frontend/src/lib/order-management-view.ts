import type {
  ArrangementOrder,
  ArrangementsResult,
  SupplyArrangement,
} from "./order-groups-api";

export type ManagementView = "po" | "voyage";
export type ManagementStatus =
  | "missing_info"
  | "failed"
  | "processing"
  | "attention"
  | "normal";

export interface StatusPresentation {
  code: ManagementStatus;
  label: string;
  count: number;
}

export interface ManagementFilters {
  search: string;
  ship: string;
  port: string;
  status: ManagementStatus | "";
  from: string;
  to: string;
  unclassifiedOnly: boolean;
}

export interface FilterOptions {
  ships: string[];
  ports: string[];
}

export interface PaginatedRows<T> {
  items: T[];
  page: number;
  pageCount: number;
  total: number;
}

export interface PoManagementRow {
  kind: "po";
  id: number;
  order: ArrangementOrder;
  arrangementId: number | null;
  arrangementCanManage: boolean;
  unclassified: boolean;
  poNumber: string;
  filename: string;
  ship: string | null;
  day: string | null;
  port: string | null;
  productCount: number;
  productNames: string[];
  status: StatusPresentation;
}

export interface VoyageManagementRow {
  kind: "arrangement" | "unclassified";
  id: string;
  arrangement: SupplyArrangement | null;
  arrangementId: number | null;
  ship: string;
  day: string | null;
  port: string | null;
  poCount: number;
  productCount: number;
  status: StatusPresentation;
}

const PROCESSING_ORDER_STATUSES = new Set([
  "uploading",
  "pending_template",
  "extracting",
  "extracted",
  "matching",
]);
const PROCESSING_INQUIRY_STATUSES = new Set([
  "pending",
  "in_progress",
  "generating",
]);

const STATUS_PRIORITY: Record<ManagementStatus, number> = {
  missing_info: 0,
  failed: 1,
  processing: 2,
  attention: 3,
  normal: 4,
};

export function managementStatusForOrder(
  order: ArrangementOrder,
): StatusPresentation {
  const hasText = (value: string | null | undefined) => Boolean(value?.trim());
  if (!hasText(order.ship) || !hasText(order.day) || !hasText(order.port)) {
    return {
      code: "missing_info",
      label: order.reason?.trim() || "需补充信息",
      count: 0,
    };
  }
  if (order.status === "error" || order.inquiry_status === "error") {
    return { code: "failed", label: "处理失败", count: 0 };
  }
  if (
    PROCESSING_ORDER_STATUSES.has(order.status) ||
    PROCESSING_INQUIRY_STATUSES.has(order.inquiry_status || "")
  ) {
    return { code: "processing", label: "处理中", count: 0 };
  }
  const count = Math.max(0, Number(order.anomaly_count) || 0);
  if (order.requires_human_review || count > 0) {
    return { code: "attention", label: `需处理 ${count} 项`, count };
  }
  return { code: "normal", label: "正常", count: 0 };
}

function poRow(
  order: ArrangementOrder,
  arrangementId: number | null,
  arrangementCanManage: boolean,
): PoManagementRow {
  return {
    kind: "po",
    id: order.id,
    order,
    arrangementId,
    arrangementCanManage,
    unclassified: arrangementId === null,
    poNumber: order.po_number || `PO #${order.id}`,
    filename: order.filename,
    ship: order.ship,
    day: order.day,
    port: order.port,
    productCount: Math.max(0, Number(order.product_count) || 0),
    productNames: Array.isArray(order.product_names)
      ? order.product_names
      : [],
    status: managementStatusForOrder(order),
  };
}

export function buildPoRows(data: ArrangementsResult): PoManagementRow[] {
  const rows: PoManagementRow[] = [];
  const seen = new Set<number>();
  for (const arrangement of data.arrangements) {
    for (const order of arrangement.orders) {
      if (seen.has(order.id)) continue;
      seen.add(order.id);
      rows.push(poRow(order, arrangement.id, arrangement.can_manage));
    }
  }
  for (const order of data.unclassified) {
    if (seen.has(order.id)) continue;
    seen.add(order.id);
    rows.push(poRow(order, null, true));
  }
  return rows.sort((left, right) => {
    const leftMissing = left.status.code === "missing_info" ? 0 : 1;
    const rightMissing = right.status.code === "missing_info" ? 0 : 1;
    return (
      leftMissing - rightMissing ||
      (right.day || "").localeCompare(left.day || "") ||
      right.id - left.id
    );
  });
}

function voyageStatus(orders: ArrangementOrder[]): StatusPresentation {
  const statuses = orders.map(managementStatusForOrder);
  const highest = statuses.reduce<StatusPresentation>(
    (current, candidate) =>
      STATUS_PRIORITY[candidate.code] < STATUS_PRIORITY[current.code]
        ? candidate
        : current,
    { code: "normal", label: "正常", count: 0 },
  );
  if (highest.code !== "attention") return { ...highest, count: 0 };
  const count = statuses.reduce(
    (sum, status) =>
      sum + (status.code === "attention" ? status.count : 0),
    0,
  );
  return { code: "attention", label: `需处理 ${count} 项`, count };
}

export function buildVoyageRows(
  data: ArrangementsResult,
): VoyageManagementRow[] {
  const rows: VoyageManagementRow[] = data.arrangements.map((arrangement) => ({
    kind: "arrangement",
    id: `arrangement-${arrangement.id}`,
    arrangement,
    arrangementId: arrangement.id,
    ship: arrangement.ship,
    day: arrangement.day,
    port: arrangement.port,
    poCount: arrangement.orders.length,
    productCount: arrangement.orders.reduce(
      (sum, order) => sum + (Number(order.product_count) || 0),
      0,
    ),
    status: voyageStatus(arrangement.orders),
  }));
  rows.sort((left, right) => {
    if (!left.day && right.day) return 1;
    if (left.day && !right.day) return -1;
    return (
      (left.day || "").localeCompare(right.day || "") ||
      left.ship.localeCompare(right.ship) ||
      (left.arrangementId || 0) - (right.arrangementId || 0)
    );
  });
  if (data.unclassified.length) {
    rows.push({
      kind: "unclassified",
      id: "unclassified",
      arrangement: null,
      arrangementId: null,
      ship: "未分配",
      day: null,
      port: null,
      poCount: data.unclassified.length,
      productCount: data.unclassified.reduce(
        (sum, order) => sum + (Number(order.product_count) || 0),
        0,
      ),
      status: { code: "missing_info", label: "需补充信息", count: 0 },
    });
  }
  return rows;
}

function includesFolded(value: string | null | undefined, term: string): boolean {
  return Boolean(value?.toLocaleLowerCase().includes(term));
}

function matchesSharedFilters(
  row: Pick<PoManagementRow | VoyageManagementRow, "ship" | "day" | "port" | "status">,
  filters: ManagementFilters,
): boolean {
  return (
    (!filters.ship || row.ship === filters.ship) &&
    (!filters.port || row.port === filters.port) &&
    (!filters.status || row.status.code === filters.status) &&
    (!filters.from || Boolean(row.day && row.day >= filters.from)) &&
    (!filters.to || Boolean(row.day && row.day <= filters.to))
  );
}

export function filterPoRows(
  rows: PoManagementRow[],
  filters: ManagementFilters,
): PoManagementRow[] {
  const term = filters.search.trim().toLocaleLowerCase();
  return rows.filter((row) => {
    const searchable = [
      row.poNumber,
      row.filename,
      row.ship,
      row.port,
      String(row.id),
      ...row.productNames,
    ];
    return (
      (!filters.unclassifiedOnly || row.unclassified) &&
      (!term || searchable.some((value) => includesFolded(value, term))) &&
      matchesSharedFilters(row, filters)
    );
  });
}

export function filterVoyageRows(
  rows: VoyageManagementRow[],
  filters: ManagementFilters,
): VoyageManagementRow[] {
  const term = filters.search.trim().toLocaleLowerCase();
  return rows.filter(
    (row) =>
      (!term ||
        includesFolded(row.ship, term) ||
        includesFolded(row.port, term)) &&
      matchesSharedFilters(row, filters),
  );
}

export function managementFilterOptions(
  rows: Array<PoManagementRow | VoyageManagementRow>,
): FilterOptions {
  const ships = new Set<string>();
  const ports = new Set<string>();
  for (const row of rows) {
    if (row.ship) ships.add(row.ship);
    if (row.port) ports.add(row.port);
  }
  return {
    ships: [...ships].sort((left, right) => left.localeCompare(right)),
    ports: [...ports].sort((left, right) => left.localeCompare(right)),
  };
}

export function paginateRows<T>(
  rows: T[],
  requestedPage: number,
  requestedPageSize: number,
): PaginatedRows<T> {
  const pageSize =
    Number.isFinite(requestedPageSize) && requestedPageSize > 0
      ? Math.trunc(requestedPageSize)
      : 1;
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const desiredPage = Number.isFinite(requestedPage)
    ? Math.trunc(requestedPage)
    : 1;
  const page = Math.min(pageCount, Math.max(1, desiredPage));
  const start = (page - 1) * pageSize;
  return {
    items: rows.slice(start, start + pageSize),
    page,
    pageCount,
    total: rows.length,
  };
}

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

export function weekdayLabel(day: string | null): string {
  if (!day || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return "";
  const date = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== day) {
    return "";
  }
  return WEEKDAYS[date.getUTCDay()];
}
