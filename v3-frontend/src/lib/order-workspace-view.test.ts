import { describe, expect, it } from "vitest";

import type { ArrangementWorkspace } from "./order-groups-api";
import {
  arrangementPoStatus,
  arrangementWorkspaceStatus,
  formatBusinessDateTime,
  orderDetailStatus,
  supplierInquiryStatus,
} from "./order-workspace-view";

function workspace(overrides: Partial<ArrangementWorkspace> = {}): ArrangementWorkspace {
  return {
    arrangement: {
      id: 1,
      name: "安排",
      ship: "DIAMOND PRINCESS",
      day: "2026-09-20",
      date_basis: "loading_date",
      port_id: 1,
      port: "横滨港",
      manual: false,
      can_manage: true,
      can_generate_inquiry: true,
      orders: [],
    },
    summary: {
      product_count: 35,
      matched_count: 35,
      unmatched_count: 0,
      supplier_count: 3,
      anomaly_count: 0,
      updated_at: null,
    },
    latest_inquiry: null,
    suppliers: [],
    unassigned_items: [],
    activity: [],
    ...overrides,
  };
}

describe("structured order workspace view", () => {
  it("gives processing and errors priority over decorative completion states", () => {
    expect(arrangementWorkspaceStatus(workspace({
      latest_inquiry: { id: 1, version: 1, status: "in_progress", started_at: null, completed_at: null, unassigned_count: 0, error_message: null, inputs_changed: false },
    }))).toEqual({ label: "询价生成中", tone: "progress" });
    expect(arrangementWorkspaceStatus(workspace({
      latest_inquiry: { id: 1, version: 1, status: "error", started_at: null, completed_at: null, unassigned_count: 0, error_message: "failed", inputs_changed: false },
    }))).toEqual({ label: "询价生成失败", tone: "danger" });
  });

  it("keeps unmatched rows visible even when an inquiry version exists", () => {
    const current = workspace({
      summary: { product_count: 3, matched_count: 2, unmatched_count: 1, supplier_count: 1, anomaly_count: 1, updated_at: null },
      latest_inquiry: { id: 1, version: 2, status: "partial", started_at: null, completed_at: null, unassigned_count: 1, error_message: null, inputs_changed: false },
    });
    expect(arrangementWorkspaceStatus(current)).toEqual({ label: "需要处理", tone: "warning" });
  });

  it("marks a completed inquiry stale after shared order fields change", () => {
    const current = workspace({
      latest_inquiry: { id: 1, version: 3, status: "completed", started_at: null, completed_at: null, unassigned_count: 0, error_message: null, inputs_changed: true },
    });
    expect(arrangementWorkspaceStatus(current)).toEqual({
      label: "订单已修改，需重新生成",
      tone: "warning",
    });
  });

  it("labels PO and supplier rows without exposing PO-level inquiry actions", () => {
    expect(arrangementPoStatus({
      id: 2, po_number: "PO-2", filename: "2.pdf", document_id: null,
      product_count: 8, ship: "SHIP", day: "2026-09-20", port: "横滨港",
      status: "ready", fulfillment_status: "pending", inquiry_status: null,
      requires_human_review: true, anomaly_count: 1, reason: null,
    })).toEqual({ label: "需要处理 1 项", tone: "warning" });
    expect(supplierInquiryStatus({
      supplier_id: 1, supplier_name: "供应商", product_count: 8,
      source_order_count: 2, source_po_numbers: ["PO-1", "PO-2"],
      status: "completed", error_message: null,
      template_id: 1, template_name: "日本订单标准", template_method: "exact",
    })).toEqual({ label: "已生成", tone: "success" });
  });

  it("uses the backend unique actionable-row count instead of raw finding totals", () => {
    expect(orderDetailStatus({
      status: "ready",
      actionable_count: 1,
      anomaly_data: {
        requires_human_review: true,
        total_anomalies: 59,
        error_count: 59,
        blocking_count: 0,
        price_anomalies: [],
        quantity_anomalies: [],
        completeness_issues: [],
      },
      match_statistics: { total: 1, matched: 0, not_matched: 1, match_rate: 0 },
    })).toEqual({ label: "需要处理 1 项", tone: "warning" });
  });

  it("formats stored UTC timestamps in the business timezone", () => {
    expect(formatBusinessDateTime("2026-09-17T07:05:00")).toContain("16:05");
    expect(formatBusinessDateTime(null)).toBe("—");
  });
});
