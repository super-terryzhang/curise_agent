/**
 * Contract tests for the pure helpers in `tool-uis.tsx`.
 *
 * The React components themselves are wired through assistant-ui and
 * tested by humans-in-browser. But every helper that decides what to
 * RENDER (column inference, result parsing, args description) is pure
 * and pinned here — those are the bits that quietly drift and break
 * "the table looks weird" before anyone notices.
 */

import { describe, expect, it } from "vitest";

import {
  describeArgs,
  inferColumns,
  parseRowsFromResult,
  truncate,
  TOOL_CONFIGS,
  buildToolUIs,
} from "./tool-uis";

// ─── truncate ───────────────────────────────────────────────

describe("truncate", () => {
  it("returns the input unchanged when within bound", () => {
    expect(truncate("hello", 10)).toBe("hello");
  });

  it("trims and appends ellipsis when over bound", () => {
    expect(truncate("hello world", 6)).toBe("hello…");
  });

  it("handles max=0 without crashing", () => {
    expect(truncate("abc", 0)).toBe("…");
  });
});

// ─── parseRowsFromResult ────────────────────────────────────

describe("parseRowsFromResult", () => {
  it("returns null on null/undefined", () => {
    expect(parseRowsFromResult(null)).toBeNull();
    expect(parseRowsFromResult(undefined)).toBeNull();
  });

  it("returns null on empty string", () => {
    expect(parseRowsFromResult("")).toBeNull();
    expect(parseRowsFromResult("   ")).toBeNull();
  });

  it("returns null on non-JSON string", () => {
    expect(parseRowsFromResult("hello, world")).toBeNull();
  });

  it("parses a JSON array of objects", () => {
    expect(
      parseRowsFromResult('[{"a":1},{"a":2}]'),
    ).toEqual([{ a: 1 }, { a: 2 }]);
  });

  it("unwraps { items: [...] }", () => {
    expect(
      parseRowsFromResult({ items: [{ id: 1 }, { id: 2 }] }),
    ).toEqual([{ id: 1 }, { id: 2 }]);
  });

  it("unwraps { rows: [...] }", () => {
    expect(parseRowsFromResult({ rows: [{ x: 1 }] })).toEqual([{ x: 1 }]);
  });

  it("unwraps { data: [...] }", () => {
    expect(parseRowsFromResult({ data: [{ x: 1 }] })).toEqual([{ x: 1 }]);
  });

  it("filters out non-object array entries", () => {
    expect(
      parseRowsFromResult([{ a: 1 }, "scalar", null, [1, 2]] as unknown),
    ).toEqual([{ a: 1 }]);
  });

  it("returns null on a status-only object (no rows)", () => {
    expect(parseRowsFromResult({ ok: true, count: 0 })).toBeNull();
  });
});

// ─── inferColumns ───────────────────────────────────────────

describe("inferColumns", () => {
  it("returns empty array for empty rows", () => {
    expect(inferColumns([])).toEqual([]);
  });

  it("prioritizes identifying columns first (id, name, code)", () => {
    const rows = [{ status: "ok", id: 1, code: "A", name: "Foo", x: 9 }];
    // id then name then code, then alphabetical (status, x)
    expect(inferColumns(rows)).toEqual(["id", "name", "code", "status", "x"]);
  });

  it("limits to maxCols", () => {
    const rows = [{ a: 1, b: 2, c: 3, d: 4, e: 5, f: 6, g: 7 }];
    expect(inferColumns(rows, 3)).toEqual(["a", "b", "c"]);
  });

  it("skips columns that contain non-scalar values", () => {
    const rows = [
      { id: 1, payload: { nested: true }, tags: [1, 2] },
      { id: 2, payload: { nested: true }, tags: [3] },
    ];
    expect(inferColumns(rows)).toEqual(["id"]);
  });
});

// ─── describeArgs ───────────────────────────────────────────

describe("describeArgs", () => {
  it("returns null on null/undefined/non-object args", () => {
    expect(describeArgs("query_db", null)).toBeNull();
    expect(describeArgs("query_db", undefined)).toBeNull();
    expect(describeArgs("query_db", "raw" as unknown)).toBeNull();
  });

  it("strips whitespace from query_db SQL", () => {
    expect(
      describeArgs("query_db", {
        sql: "  SELECT *\nFROM orders\n WHERE id=1",
      }),
    ).toBe("SELECT * FROM orders WHERE id=1");
  });

  it("truncates long SQL to 60 chars", () => {
    const longSql = "SELECT " + "a, ".repeat(40) + "z FROM t";
    const out = describeArgs("query_db", { sql: longSql });
    expect(out).toBeTruthy();
    expect(out!.length).toBeLessThanOrEqual(60);
    expect(out!.endsWith("…")).toBe(true);
  });

  it("renders `订单 #<id>` for order-id-keyed tools", () => {
    expect(describeArgs("get_order_detail", { order_id: 7 })).toBe("订单 #7");
    expect(describeArgs("update_order", { order_id: 7 })).toBe("订单 #7");
    expect(describeArgs("rematch_order", { order_id: 7 })).toBe("订单 #7");
    expect(describeArgs("generate_inquiry", { order_id: 7 })).toBe("订单 #7");
  });

  it("renders `文档 #<id>` for document-keyed tools", () => {
    expect(describeArgs("get_document", { document_id: 3 })).toBe("文档 #3");
    expect(describeArgs("read_document_section", { document_id: 3 })).toBe(
      "文档 #3",
    );
  });

  it("describes batch + row for inspect_upload_row", () => {
    expect(
      describeArgs("inspect_upload_row", { batch_id: 5, row_index: 12 }),
    ).toBe("批次 #5 第 12 行");
  });

  it("falls back to batch-only when row missing", () => {
    expect(
      describeArgs("inspect_upload_row", { batch_id: 5 }),
    ).toBe("批次 #5");
  });

  it("returns host for web_fetch URL", () => {
    expect(describeArgs("web_fetch", { url: "https://example.com/foo" })).toBe(
      "example.com",
    );
  });

  it("returns truncated URL when not parsable", () => {
    expect(describeArgs("web_fetch", { url: "not a url" })).toBe("not a url");
  });

  it("returns null for unknown tool names", () => {
    expect(describeArgs("does_not_exist", { foo: "bar" })).toBeNull();
  });
});

// ─── TOOL_CONFIGS coverage ──────────────────────────────────

describe("TOOL_CONFIGS / buildToolUIs", () => {
  // We don't want the registry to silently lose entries during refactors,
  // so this test pins the expected names. If you add or remove a tool,
  // update this list deliberately.
  const EXPECTED_TOOLS = [
    "query_db",
    "search_masterdata",
    "list_orders",
    "list_documents",
    "list_my_uploads",
    "search_documents",
    "web_search",
    "get_order_detail",
    "get_document",
    "read_document_section",
    "inspect_upload_row",
    "get_upload_template",
    "parse_uploaded_file",
    "preview_upload",
    "update_order",
    "update_product",
    "update_supplier",
    "rematch_order",
    "generate_inquiry",
    "regenerate_supplier_inquiry",
    "web_fetch",
    "load_skill",
    "remember",
    "recall",
    "present_artifact",
  ];

  it("registers every expected backend tool", () => {
    const registered = Object.keys(TOOL_CONFIGS).sort();
    expect(registered).toEqual([...EXPECTED_TOOLS].sort());
  });

  it("buildToolUIs yields one component per registered tool", () => {
    const uis = buildToolUIs();
    for (const name of EXPECTED_TOOLS) {
      expect(uis[name]).toBeTypeOf("function");
    }
  });

  it("buildToolUIs is cached — same call returns same identity", () => {
    expect(buildToolUIs()).toBe(buildToolUIs());
  });
});
