import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type {
  PoManagementRow,
  VoyageManagementRow,
} from "@/lib/order-management-view";
import {
  canRemovePo,
  PoManagementTable,
  VoyageManagementTable,
} from "./OrderManagementTables";

const poRow: PoManagementRow = {
  kind: "po",
  id: 1,
  order: {
    id: 1,
    po_number: "PO-001",
    filename: "po-001.pdf",
    document_id: null,
    product_count: 7,
    product_names: ["Rare Saffron"],
    ship: "SILVER MUSE",
    day: "2026-06-05",
    port: "シンガポール",
    status: "ready",
    fulfillment_status: "pending",
    inquiry_status: "completed",
    requires_human_review: true,
    anomaly_count: 2,
    reason: null,
  },
  arrangementId: 10,
  arrangementCanManage: true,
  unclassified: false,
  poNumber: "PO-001",
  filename: "po-001.pdf",
  ship: "SILVER MUSE",
  day: "2026-06-05",
  port: "シンガポール",
  productCount: 7,
  productNames: ["Rare Saffron"],
  status: { code: "attention", label: "需处理 2 项", count: 2 },
};

const pendingPortResolution = {
  method: "llm" as const,
  status: "pending_review" as const,
  source_destination: "OSAKA",
  source_port_code: null,
  suggested_port_id: 21,
  final_port_id: 21,
  model: "gemini",
  prompt_version: "v1",
  decision_id: "decision-1",
  reason: "matched",
  decided_at: null,
  failure_code: null,
  reviewed_by: null,
  reviewed_at: null,
};

const voyageRow: VoyageManagementRow = {
  kind: "arrangement",
  id: "arrangement-10",
  arrangement: {
    id: 10,
    name: "SILVER MUSE 2026-06-05",
    ship: "SILVER MUSE",
    day: "2026-06-05",
    date_basis: "loading_date",
    port_id: 1,
    port: "シンガポール",
    manual: false,
    can_manage: true,
    can_generate_inquiry: true,
    orders: [poRow.order],
  },
  arrangementId: 10,
  ship: "SILVER MUSE",
  day: "2026-06-05",
  port: "シンガポール",
  poCount: 2,
  productCount: 7,
  status: { code: "normal", label: "正常", count: 0 },
};

const unclassifiedRow: VoyageManagementRow = {
  kind: "unclassified",
  id: "unclassified",
  arrangement: null,
  arrangementId: null,
  ship: "未分配",
  day: null,
  port: null,
  poCount: 3,
  productCount: 12,
  status: { code: "missing_info", label: "需补充信息", count: 0 },
};

describe("order management tables", () => {
  it("keeps the remove action for a user's PO inside a shared arrangement", () => {
    expect(canRemovePo({ ...poRow, arrangementCanManage: false })).toBe(true);
    expect(
      canRemovePo({
        ...poRow,
        arrangementId: null,
        arrangementCanManage: false,
        unclassified: true,
      }),
    ).toBe(false);
  });

  it("renders direct PO columns, status text, detail link and actions", () => {
    const html = renderToStaticMarkup(
      <PoManagementTable
        rows={[poRow]}
        busy={false}
        onAssign={() => undefined}
        onRemove={() => undefined}
        onReclassify={() => undefined}
        onDelete={() => undefined}
      />,
    );

    for (const heading of [
      "PO 编号",
      "船名",
      "装船日期",
      "目标港口",
      "商品数",
      "处理状态",
      "操作",
    ]) {
      expect(html).toContain(heading);
    }
    expect(html).toContain("PO-001");
    expect(html).toContain("SILVER MUSE");
    expect(html).toContain("需处理 2 项");
    expect(html).toContain('href="/dashboard/orders/1"');
    expect(html).toContain("PO-001 更多操作");
  });

  it("renders voyage totals, weekday, detail link and unclassified action", () => {
    const html = renderToStaticMarkup(
      <VoyageManagementTable
        rows={[voyageRow, unclassifiedRow]}
        onShowUnclassified={() => undefined}
      />,
    );

    for (const heading of [
      "装船日期",
      "船名",
      "目标港口",
      "PO 数",
      "商品数",
      "处理状态",
      "操作",
    ]) {
      expect(html).toContain(heading);
    }
    expect(html).toContain("周五");
    expect(html).toContain("SILVER MUSE");
    expect(html).toContain('href="/dashboard/orders/arrangements/10"');
    expect(html).toContain("未分配");
    expect(html).toContain("需补充信息");
    expect(html).toContain("查看未分配 PO");
  });

  it("preserves pending AI port review signals in both views", () => {
    const pendingPo = {
      ...poRow,
      order: { ...poRow.order, port_resolution: pendingPortResolution },
      status: { code: "attention" as const, label: "港口待确认", count: 0 },
    };
    const poHtml = renderToStaticMarkup(
      <PoManagementTable
        rows={[pendingPo]}
        busy={false}
        onAssign={() => undefined}
        onRemove={() => undefined}
        onReclassify={() => undefined}
        onDelete={() => undefined}
      />,
    );
    const voyageHtml = renderToStaticMarkup(
      <VoyageManagementTable
        rows={[
          {
            ...voyageRow,
            arrangement: {
              ...voyageRow.arrangement!,
              pending_port_review_count: 1,
              orders: [pendingPo.order],
            },
            status: pendingPo.status,
          },
        ]}
        onShowUnclassified={() => undefined}
      />,
    );

    expect(poHtml).toContain("AI 匹配 · 待人工确认");
    expect(voyageHtml).toContain("1 个 PO 港口待确认");
    expect(voyageHtml).toContain("港口待确认");
  });

  it("renders explicit empty states", () => {
    const poHtml = renderToStaticMarkup(
      <PoManagementTable
        rows={[]}
        busy={false}
        onAssign={() => undefined}
        onRemove={() => undefined}
        onReclassify={() => undefined}
        onDelete={() => undefined}
      />,
    );
    const voyageHtml = renderToStaticMarkup(
      <VoyageManagementTable rows={[]} onShowUnclassified={() => undefined} />,
    );

    expect(poHtml).toContain("没有符合筛选的 PO");
    expect(voyageHtml).toContain("没有符合筛选的轮次");
  });
});
