import type {
  ArrangementOrder,
  ArrangementWorkspace,
  ArrangementWorkspaceSupplier,
} from "./order-groups-api";

export type BusinessTone = "neutral" | "success" | "warning" | "danger" | "progress";

export interface BusinessStatus {
  label: string;
  tone: BusinessTone;
}

export function arrangementWorkspaceStatus(
  workspace: ArrangementWorkspace,
): BusinessStatus {
  const status = workspace.latest_inquiry?.status;
  if (status === "pending" || status === "in_progress") {
    return { label: "询价生成中", tone: "progress" };
  }
  if (status === "error") {
    return { label: "询价生成失败", tone: "danger" };
  }
  if (workspace.latest_inquiry?.inputs_changed) {
    return { label: "订单已修改，需重新生成", tone: "warning" };
  }
  if (workspace.summary.anomaly_count > 0 || workspace.summary.unmatched_count > 0) {
    return { label: "需要处理", tone: "warning" };
  }
  if (status === "completed") {
    return { label: "询价已生成", tone: "success" };
  }
  if (status === "partial" || status === "unmatched") {
    return { label: "询价部分完成", tone: "warning" };
  }
  return { label: "待生成询价", tone: "neutral" };
}

export function arrangementPoStatus(order: ArrangementOrder): BusinessStatus {
  if (order.status === "error") return { label: "处理失败", tone: "danger" };
  if (["uploading", "extracting", "matching"].includes(order.status)) {
    return { label: "自动处理中", tone: "progress" };
  }
  const attention = order.anomaly_count || order.unmatched_count || 0;
  if (order.requires_human_review || attention > 0) {
    return { label: `需要处理${attention ? ` ${attention} 项` : ""}`, tone: "warning" };
  }
  return { label: "检查完成", tone: "success" };
}

export function supplierInquiryStatus(
  supplier: ArrangementWorkspaceSupplier,
): BusinessStatus {
  if (supplier.status === "completed") return { label: "已生成", tone: "success" };
  if (supplier.status === "generating" || supplier.status === "in_progress") {
    return { label: "生成中", tone: "progress" };
  }
  if (supplier.status === "error") return { label: "生成失败", tone: "danger" };
  if (supplier.status === "cancelled") return { label: "已取消", tone: "neutral" };
  return { label: "未生成", tone: "neutral" };
}

export function formatBusinessDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value.endsWith("Z") || value.includes("+") ? value : `${value}Z`);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Tokyo",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}
