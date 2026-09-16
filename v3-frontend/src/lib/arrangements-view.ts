import type { ArrangementOrder, SupplyArrangement } from "./order-groups-api";
export const PIPELINE_LABELS: Record<string, string> = {
  uploading: "上传中", pending_template: "待选模板", extracting: "分析中",
  extracted: "待匹配", matching: "匹配中", ready: "分析完成", error: "分析失败",
};
export const INQUIRY_LABELS: Record<string, string> = {
  pending: "询价文件待生成", in_progress: "询价文件生成中", generating: "文件生成中",
  completed: "询价文件已生成", partial: "询价部分完成", unmatched: "暂无可生成商品",
  error: "询价文件生成失败", cancelled: "询价生成已取消",
};

export interface ArrangementFilters { search: string; from: string; to: string }
export function matchesOrder(order: ArrangementOrder, filters: ArrangementFilters): boolean {
  const term = filters.search.trim().toLocaleLowerCase();
  return (!term || [order.po_number, order.ship, order.port, order.filename, String(order.id)].some(v => v?.toLocaleLowerCase().includes(term)))
    && (!filters.from || !!order.day && order.day >= filters.from)
    && (!filters.to || !!order.day && order.day <= filters.to);
}
export function filterArrangements(groups: SupplyArrangement[], filters: ArrangementFilters, today: string) {
  return groups.filter(group => {
    const term = filters.search.trim().toLocaleLowerCase();
    const groupMatch = [group.name, group.ship, group.port].some(v => v.toLocaleLowerCase().includes(term));
    return group.orders.some(order => matchesOrder(order, groupMatch ? { ...filters, search: "" } : filters));
  }).sort((a, b) => {
    const rank = (day: string | null) => !day ? 2 : day >= today ? 0 : 1;
    return rank(a.day) - rank(b.day)
      || (rank(a.day) === 0 ? (a.day || "").localeCompare(b.day || "") : (b.day || "").localeCompare(a.day || ""))
      || a.ship.localeCompare(b.ship) || a.id - b.id;
  });
}
export function poProcessingLabel(order: ArrangementOrder): string {
  if (order.requires_human_review) return `需处理 ${order.anomaly_count || 0} 项`;
  if (order.status === "error") return PIPELINE_LABELS.error;
  if (order.inquiry_status === "error") return INQUIRY_LABELS.error;
  if (order.status === "ready" && order.inquiry_status === "completed") return "自动处理完成";
  if (order.inquiry_status) return INQUIRY_LABELS[order.inquiry_status] || "询价状态待确认";
  return PIPELINE_LABELS[order.status] || "处理状态待确认";
}

export function arrangementPath(groupId: number): string {
  return `/dashboard/orders/arrangements/${groupId}`;
}
