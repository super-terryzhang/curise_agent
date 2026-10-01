import { describe, expect, it } from "vitest";
import type { ProductItem } from "./data-api";
import {
  matchPeriodTargets,
  createEditRows,
  applyUniformValues,
  buildDirectUpdateRequest,
  rowIssues,
} from "./product-batch-edit";

const a = {
  id: 1,
  revision: 2,
  code: "0001",
  product_name_en: "Apple",
  currency: "JPY",
  country_id: 1,
  port_id: 1,
  country_name: "Japan",
  port_name: "Osaka",
  price_periods: [
    {
      id: 10,
      price_type: "purchase",
      amount: 12,
      currency: "JPY",
      status: true,
      effective_from: "2026-04-01",
      effective_to: "2026-06-30",
    },
    {
      id: 11,
      price_type: "selling",
      amount: 80,
      currency: "JPY",
      status: true,
      effective_from: "2026-04-01",
      effective_to: "2026-06-30",
    },
    {
      id: 12,
      price_type: "purchase",
      amount: 20,
      currency: "JPY",
      status: false,
      effective_from: "2026-01-01",
      effective_to: "2026-03-31",
    },
  ],
} as ProductItem;
const b = {
  ...a,
  id: 2,
  code: "0002",
  product_name_en: "Banana",
  price_periods: [
    { ...a.price_periods![0], id: 20, product_id: 2, amount: 78 },
  ],
} as ProductItem;

describe("direct product batch edit", () => {
  it("targets only an exact active period and lists missing products", () => {
    const result = matchPeriodTargets(
      [a, b],
      "purchase",
      "2026-01-01",
      "2026-03-31",
    );
    expect(result.targets).toHaveLength(0);
    expect(result.missing.map((p) => p.id)).toEqual([1, 2]);
    const matches = matchPeriodTargets(
      [a, b],
      "selling",
      "2026-04-01",
      "2026-06-30",
    );
    expect(matches.targets.map((t) => t.period.id)).toEqual([11]);
    expect(matches.missing.map((p) => p.id)).toEqual([2]);
  });
  it("uniform end date preserves each amount and updates child IDs only", () => {
    const target = matchPeriodTargets(
      [a, b],
      "purchase",
      "2026-04-01",
      "2026-06-30",
    );
    const rows = createEditRows([a, b], "purchase", "edit", target.targets);
    const changed = applyUniformValues(rows, { effective_to: "2026-07-31" });
    expect(changed.map((r) => r.values.amount)).toEqual(["12", "78"]);
    expect(changed.map((r) => r.values.effective_from)).toEqual([
      "2026-04-01",
      "2026-04-01",
    ]);
    const req = buildDirectUpdateRequest([a, b], "purchase", "edit", changed);
    expect(req.rows.map((r) => r.period_id)).toEqual([10, 20]);
    expect(req.rows.map((r) => r.values)).toEqual([
      { effective_to: "2026-07-31" },
      { effective_to: "2026-07-31" },
    ]);
  });
  it("new periods leave prices empty instead of guessing existing product prices", () => {
    const rows = createEditRows([a, b], "purchase", "add");
    expect(rows.map((r) => r.values.amount)).toEqual(["", ""]);
    expect(rowIssues(rows[0], "purchase")).toContain("请填写价格");
  });
  it("zero new price is valid and a second interval has no old period ID", () => {
    const row = createEditRows([a], "selling", "add")[0];
    const first = {
      ...row,
      values: {
        amount: "0",
        currency: "JPY",
        effective_from: "2026-08-01",
        effective_to: "2026-09-30",
      },
    };
    const second = {
      ...first,
      key: "extra",
      values: {
        ...first.values,
        amount: "20",
        effective_from: "2026-10-01",
        effective_to: "2026-12-31",
      },
    };
    expect(rowIssues(first, "selling")).toEqual([]);
    const req = buildDirectUpdateRequest([a], "selling", "add", [
      first,
      second,
    ]);
    expect(req.rows).toHaveLength(2);
    expect(req.rows.map((r) => r.period_id)).toEqual([null, null]);
    expect(req.rows[0].values.amount).toBe("0");
  });
  it("basic changes keep ID and revision and omit untouched fields", () => {
    const rows = createEditRows([a, b], "basic", "edit");
    rows[0].values.product_name_en = "Renamed";
    rows[0].values.supplier_id = 4;
    const req = buildDirectUpdateRequest([a, b], "basic", "edit", rows);
    expect(req.rows).toEqual([
      {
        product_id: 1,
        expected_revision: 2,
        period_id: null,
        values: { product_name_en: "Renamed", supplier_id: 4 },
      },
    ]);
  });
  it("rejects reversed or invalid dates and invalid amounts before prepare", () => {
    const row = createEditRows([a], "purchase", "add")[0];
    row.values = {
      amount: "-1",
      effective_from: "2026-13-01",
      effective_to: "2026-01-01",
      currency: "JPY",
    };
    expect(rowIssues(row, "purchase").length).toBeGreaterThan(0);
    expect(() =>
      buildDirectUpdateRequest([a], "purchase", "add", [row]),
    ).toThrow();
  });
});
