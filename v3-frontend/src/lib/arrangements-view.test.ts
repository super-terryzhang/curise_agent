import { describe, expect, it } from "vitest";
import {
  arrangementPath,
  filterArrangements,
  pendingPortReviewSummary,
  poProcessingLabel,
} from "./arrangements-view";
import type { SupplyArrangement, ArrangementOrder } from "./order-groups-api";
const order = (id: number, day: string): ArrangementOrder => ({ id, day, po_number: `PO-${id}`, filename: `${id}.pdf`, document_id: null, product_count: 3, product_names: [], ship: "SHIP", port: "TOKYO", status: "ready", fulfillment_status: "pending", inquiry_status: "completed", reason: null });
const group = (id: number, day: string, orders = [order(id, day)]): SupplyArrangement => ({ id, day, name: "SHIP", ship: "SHIP", port_id: 1, port: "TOKYO", date_basis: "loading_date", manual: false, can_manage: true, can_generate_inquiry: true, orders });
const filters = { search: "", from: "", to: "" };
describe("arrangement navigation", () => {
  it("finds any PO and keeps the entire arrangement available", () => {
    const g = group(1, "2026-09-11", [order(1, "2026-09-11"), order(2, "2026-09-11")]);
    expect(filterArrangements([g], { ...filters, search: "PO-2" }, "2026-09-09")[0].orders).toHaveLength(2);
  });
  it("places upcoming nearest first, then recent history", () => {
    const result = filterArrangements([group(1, "2026-01-01"), group(2, "2026-09-12"), group(3, "2026-09-10"), group(4, "2026-08-01")], filters, "2026-09-09");
    expect(result.map(g => g.id)).toEqual([3, 2, 4, 1]);
  });
  it("filters dates without coupling the list to fulfillment state", () => {
    const g = group(1, "2026-09-11");
    expect(filterArrangements([g], { ...filters, from: "2026-09-12" }, "2026-09-09")).toEqual([]);
  });
  it("labels completed and human-review PO rows without calling them orders", () => {
    expect(poProcessingLabel(order(1, "2026-09-11"))).toBe("自动处理完成");
    expect(poProcessingLabel({ ...order(2, "2026-09-11"), requires_human_review: true, anomaly_count: 2 })).toBe("需处理 2 项");
    expect(poProcessingLabel({
      ...order(3, "2026-09-11"),
      requires_human_review: true,
      anomaly_count: 0,
      port_resolution: {
        method: "llm", status: "pending_review", source_destination: "OSAKA",
        source_port_code: null, suggested_port_id: 21, final_port_id: 21,
        model: "gemini", prompt_version: "v1", decision_id: "d3",
        reason: "matched", decided_at: null, failure_code: null,
        reviewed_by: null, reviewed_at: null,
      },
    })).toBe("港口待确认");
  });
  it("builds the dedicated complete-order route", () => {
    expect(arrangementPath(42)).toBe("/dashboard/orders/arrangements/42");
  });

  it("summarizes only PO ports that still need human confirmation", () => {
    const orders = [
      {
        ...order(1, "2026-09-11"),
        port_resolution: {
          method: "llm" as const,
          status: "pending_review" as const,
          source_destination: "OSAKA",
          source_port_code: null,
          suggested_port_id: 21,
          final_port_id: 21,
          model: "gemini",
          prompt_version: "v1",
          decision_id: "d1",
          reason: "matched",
          decided_at: null,
          failure_code: null,
          reviewed_by: null,
          reviewed_at: null,
        },
      },
      {
        ...order(2, "2026-09-11"),
        port_resolution: {
          method: "llm" as const,
          status: "confirmed" as const,
          source_destination: "TOKYO",
          source_port_code: null,
          suggested_port_id: 1,
          final_port_id: 1,
          model: "gemini",
          prompt_version: "v1",
          decision_id: "d2",
          reason: null,
          decided_at: null,
          failure_code: null,
          reviewed_by: 1,
          reviewed_at: "2026-09-11T00:00:00Z",
        },
      },
      order(3, "2026-09-11"),
    ];

    expect(pendingPortReviewSummary(orders)).toBe(
      "1 个 PO 的目标港口由 AI 匹配，待人工确认",
    );
    expect(pendingPortReviewSummary(orders.slice(1))).toBeNull();
  });
});
