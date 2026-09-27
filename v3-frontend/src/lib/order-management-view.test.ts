import { describe, expect, it } from "vitest";
import type {
  ArrangementOrder,
  ArrangementsResult,
  SupplyArrangement,
} from "./order-groups-api";
import {
  buildPoRows,
  buildVoyageRows,
  filterPoRows,
  filterVoyageRows,
  managementFilterOptions,
  managementPagesAfterViewChange,
  managementStatusForOrder,
  paginateRows,
  weekdayLabel,
} from "./order-management-view";
import type { ManagementFilters } from "./order-management-view";

const makeOrder = (
  id: number,
  overrides: Partial<ArrangementOrder> = {},
): ArrangementOrder => ({
  id,
  po_number: `PO-${id}`,
  filename: `${id}.pdf`,
  document_id: null,
  product_count: 2,
  product_names: [`Product ${id}`],
  ship: "SILVER MUSE",
  day: "2026-06-05",
  port: "シンガポール",
  status: "ready",
  fulfillment_status: "pending",
  inquiry_status: "completed",
  requires_human_review: false,
  anomaly_count: 0,
  reason: null,
  ...overrides,
});

const makeGroup = (
  id: number,
  orders: ArrangementOrder[],
  overrides: Partial<SupplyArrangement> = {},
): SupplyArrangement => ({
  id,
  name: `Arrangement ${id}`,
  ship: orders[0]?.ship || "SILVER MUSE",
  day: orders[0]?.day || "2026-06-05",
  date_basis: "loading_date",
  port_id: 1,
  port: orders[0]?.port || "シンガポール",
  manual: false,
  can_manage: true,
  can_generate_inquiry: true,
  orders,
  ...overrides,
});

const makeData = (
  arrangements: SupplyArrangement[],
  unclassified: ArrangementOrder[] = [],
): ArrangementsResult => ({
  arrangements,
  unclassified,
  total_orders:
    arrangements.reduce((sum, group) => sum + group.orders.length, 0) +
    unclassified.length,
});

const filters = (
  overrides: Partial<ManagementFilters> = {},
): ManagementFilters => ({
  search: "",
  ship: "",
  port: "",
  status: "",
  from: "",
  to: "",
  unclassifiedOnly: false,
  ...overrides,
});

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

describe("order management normalization", () => {
  it("flattens classified and unclassified POs exactly once", () => {
    const first = makeOrder(1);
    const second = makeOrder(2, { day: "2026-06-12" });
    const unclassified = makeOrder(3, {
      day: null,
      port: null,
      reason: "缺少或无法识别装船日；请选择目标港口",
    });

    const rows = buildPoRows(
      makeData([makeGroup(10, [first, second])], [unclassified]),
    );

    expect(rows.map((row) => row.id)).toEqual([3, 2, 1]);
    expect(rows.find((row) => row.id === 1)).toMatchObject({
      arrangementId: 10,
      unclassified: false,
    });
    expect(rows.find((row) => row.id === 3)).toMatchObject({
      arrangementId: null,
      unclassified: true,
      status: {
        code: "missing_info",
        label: "缺少或无法识别装船日；请选择目标港口",
      },
    });
  });

  it("uses deterministic PO status priority and current actionable counts", () => {
    expect(
      managementStatusForOrder(
        makeOrder(1, {
          ship: null,
          status: "error",
          requires_human_review: true,
          anomaly_count: 4,
          reason: "请补充船名",
        }),
      ),
    ).toEqual({ code: "missing_info", label: "请补充船名", count: 0 });
    expect(
      managementStatusForOrder(
        makeOrder(2, {
          status: "error",
          inquiry_status: "in_progress",
          requires_human_review: true,
          anomaly_count: 3,
        }),
      ),
    ).toEqual({ code: "failed", label: "处理失败", count: 0 });
    expect(
      managementStatusForOrder(
        makeOrder(3, {
          status: "matching",
          requires_human_review: true,
          anomaly_count: 2,
        }),
      ),
    ).toEqual({ code: "processing", label: "处理中", count: 0 });
    expect(
      managementStatusForOrder(
        makeOrder(4, { requires_human_review: true, anomaly_count: 2 }),
      ),
    ).toEqual({ code: "attention", label: "需处理 2 项", count: 2 });
    expect(managementStatusForOrder(makeOrder(5))).toEqual({
      code: "normal",
      label: "正常",
      count: 0,
    });
  });

  it("treats whitespace-only key information and reasons as missing", () => {
    expect(
      managementStatusForOrder(
        makeOrder(6, { ship: "   ", reason: "   " }),
      ),
    ).toEqual({
      code: "missing_info",
      label: "需补充信息",
      count: 0,
    });
  });

  it("keeps pending AI port review visible in PO and voyage status", () => {
    const pending = makeOrder(7, {
      port_resolution: pendingPortResolution,
      requires_human_review: true,
      anomaly_count: 0,
    });

    expect(managementStatusForOrder(pending)).toEqual({
      code: "attention",
      label: "港口待确认",
      count: 0,
    });
    expect(buildVoyageRows(makeData([makeGroup(70, [pending])]))[0].status)
      .toEqual({
        code: "attention",
        label: "港口待确认",
        count: 0,
      });
  });

  it("never describes a zero-count human review as zero actionable items", () => {
    const reviewOnly = makeOrder(8, {
      requires_human_review: true,
      anomaly_count: 0,
    });

    expect(managementStatusForOrder(reviewOnly)).toEqual({
      code: "attention",
      label: "需人工确认",
      count: 0,
    });
    expect(buildVoyageRows(makeData([makeGroup(80, [reviewOnly])]))[0].status)
      .toEqual({
        code: "attention",
        label: "需人工确认",
        count: 0,
      });
  });

  it("aggregates voyage counts and keeps the unclassified bucket last", () => {
    const actionable = makeOrder(1, {
      product_count: 3,
      requires_human_review: true,
      anomaly_count: 2,
    });
    const failed = makeOrder(2, {
      product_count: 4,
      status: "error",
    });
    const unclassified = makeOrder(3, {
      product_count: 5,
      day: null,
      port: null,
    });

    const rows = buildVoyageRows(
      makeData([makeGroup(20, [actionable, failed])], [unclassified]),
    );

    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({
      kind: "arrangement",
      arrangementId: 20,
      poCount: 2,
      productCount: 7,
      status: { code: "failed", label: "处理失败", count: 0 },
    });
    expect(rows[1]).toMatchObject({
      kind: "unclassified",
      arrangementId: null,
      ship: "未分配",
      poCount: 1,
      productCount: 5,
      status: { code: "missing_info", label: "需补充信息", count: 0 },
      pendingPortReviewCount: 0,
    });
  });

  it("counts pending AI port reviews in the unclassified voyage bucket", () => {
    const pending = makeOrder(9, {
      day: null,
      port: null,
      port_resolution: pendingPortResolution,
      requires_human_review: true,
      anomaly_count: 0,
    });

    const row = buildVoyageRows(makeData([], [pending]))[0];

    expect(row).toMatchObject({
      kind: "unclassified",
      pendingPortReviewCount: 1,
    });
  });

  it("sums current actionable counts for an attention voyage", () => {
    const rows = buildVoyageRows(
      makeData([
        makeGroup(30, [
          makeOrder(1, { requires_human_review: true, anomaly_count: 2 }),
          makeOrder(2, { requires_human_review: true, anomaly_count: 3 }),
        ]),
      ]),
    );

    expect(rows[0].status).toEqual({
      code: "attention",
      label: "需处理 5 项",
      count: 5,
    });
  });
});

describe("order management filters and pagination", () => {
  it("resets the destination page whenever the management view changes", () => {
    expect(
      managementPagesAfterViewChange("voyage", { poPage: 4, voyagePage: 3 }),
    ).toEqual({ poPage: 4, voyagePage: 1 });
    expect(
      managementPagesAfterViewChange("po", { poPage: 4, voyagePage: 3 }),
    ).toEqual({ poPage: 1, voyagePage: 3 });
  });

  it("searches each PO's own raw product names without cross-row leakage", () => {
    const rows = buildPoRows(
      makeData([
        makeGroup(10, [
          makeOrder(1, {
            product_names: ["Rare Saffron Threads"],
            filename: "first.pdf",
          }),
          makeOrder(2, {
            product_names: ["Common Salt"],
            filename: "second.pdf",
          }),
        ]),
      ]),
    );

    expect(filterPoRows(rows, filters({ search: "SAFFRON" })).map((row) => row.id))
      .toEqual([1]);
    expect(filterPoRows(rows, filters({ search: "second.pdf" })).map((row) => row.id))
      .toEqual([2]);
    expect(filterPoRows(rows, filters({ search: "absent" }))).toEqual([]);
  });

  it("ANDs exact PO filters, inclusive dates, status and unclassified scope", () => {
    const selected = makeOrder(1, {
      ship: "SILVER CLOUD",
      port: "東京",
      day: "2026-09-19",
      requires_human_review: true,
      anomaly_count: 2,
    });
    const wrongPort = makeOrder(2, {
      ship: "SILVER CLOUD",
      port: "大阪",
      day: "2026-09-19",
      requires_human_review: true,
      anomaly_count: 2,
    });
    const unclassified = makeOrder(3, {
      ship: "SILVER CLOUD",
      port: "東京",
      day: "2026-09-19",
      requires_human_review: true,
      anomaly_count: 2,
    });
    const rows = buildPoRows(
      makeData([makeGroup(10, [selected, wrongPort])], [unclassified]),
    );
    const combined = filters({
      search: "PO-",
      ship: "SILVER CLOUD",
      port: "東京",
      status: "attention",
      from: "2026-09-19",
      to: "2026-09-19",
    });

    expect(filterPoRows(rows, combined).map((row) => row.id)).toEqual([3, 1]);
    expect(
      filterPoRows(rows, { ...combined, unclassifiedOnly: true }).map(
        (row) => row.id,
      ),
    ).toEqual([3]);
    expect(
      filterPoRows(rows, { ...combined, from: "2026-09-20" }),
    ).toEqual([]);
  });

  it("limits voyage search to ship and port and applies all exact filters", () => {
    const tokyo = makeGroup(
      10,
      [makeOrder(1, { product_names: ["Hidden Search Product"] })],
      { ship: "CELEBRITY MILLENNIUM", port: "東京", day: "2026-09-23" },
    );
    const osaka = makeGroup(20, [makeOrder(2)], {
      ship: "DIAMOND PRINCESS",
      port: "大阪",
      day: "2026-09-24",
    });
    const rows = buildVoyageRows(makeData([tokyo, osaka]));

    expect(filterVoyageRows(rows, filters({ search: "millennium" })).map((row) => row.arrangementId))
      .toEqual([10]);
    expect(filterVoyageRows(rows, filters({ search: "Hidden Search Product" })))
      .toEqual([]);
    expect(
      filterVoyageRows(
        rows,
        filters({
          ship: "DIAMOND PRINCESS",
          port: "大阪",
          status: "normal",
          from: "2026-09-24",
          to: "2026-09-24",
        }),
      ).map((row) => row.arrangementId),
    ).toEqual([20]);
  });

  it("sorts PO dates descending and voyage dates ascending with missing rows last", () => {
    const poRows = buildPoRows(
      makeData([
        makeGroup(10, [makeOrder(1, { day: "2026-06-05" })]),
        makeGroup(20, [makeOrder(2, { day: "2026-07-10" })]),
      ], [makeOrder(3, { day: null, reason: "缺少装船日" })]),
    );
    expect(poRows.map((row) => row.id)).toEqual([3, 2, 1]);

    const voyageRows = buildVoyageRows(
      makeData(
        [
          makeGroup(10, [makeOrder(1)], { day: "2026-07-10" }),
          makeGroup(20, [makeOrder(2)], { day: null, ship: "NO DATE" }),
          makeGroup(30, [makeOrder(3)], { day: "2026-06-05" }),
        ],
        [makeOrder(4, { day: null })],
      ),
    );
    expect(voyageRows.map((row) => row.id)).toEqual([
      "arrangement-30",
      "arrangement-10",
      "arrangement-20",
      "unclassified",
    ]);
  });

  it("returns stable unique filter options and weekday labels", () => {
    const rows = buildPoRows(
      makeData([
        makeGroup(10, [
          makeOrder(1, { ship: "SILVER MUSE", port: "東京" }),
          makeOrder(2, { ship: "SILVER MUSE", port: "大阪" }),
          makeOrder(3, { ship: null, port: null }),
        ]),
      ]),
    );

    expect(managementFilterOptions(rows)).toEqual({
      ships: ["SILVER MUSE"],
      ports: ["大阪", "東京"],
    });
    expect(weekdayLabel("2026-06-05")).toBe("周五");
    expect(weekdayLabel(null)).toBe("");
    expect(weekdayLabel("not-a-date")).toBe("");
  });

  it("clamps pagination after filtering and handles empty results", () => {
    expect(paginateRows([1, 2, 3, 4, 5], 9, 2)).toEqual({
      items: [5],
      page: 3,
      pageCount: 3,
      total: 5,
    });
    expect(paginateRows([], 3, 20)).toEqual({
      items: [],
      page: 1,
      pageCount: 1,
      total: 0,
    });
  });
});
