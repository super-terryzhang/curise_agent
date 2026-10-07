import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const status = {
  enabled: true,
  mode: "temporary" as const,
  database_name: "cruise_v3_clean",
  schema_version: 7,
  field_count: 10,
  product_count: 2,
  price_period_count: 5,
};

const batch = {
  id: "batch-1",
  filename: "products.xlsx",
  status: "failed",
  total_rows: 2,
  counts: { create: 1, update: 0, skip: 0, warning: 0, block: 1 },
  can_commit: false,
};

describe("temporary database setup workflow", () => {
  it("shows the exact isolated database and current preparation facts", async () => {
    const { DatabaseSetupView } = await import("./page");
    const html = renderToStaticMarkup(
      <DatabaseSetupView
        step={1}
        status={status}
        batch={null}
        rows={null}
        result={null}
        busy={false}
        error={null}
        selectedFile={null}
        onStep={vi.fn()}
        onFile={vi.fn()}
        onDownload={vi.fn()}
        onRowsPage={vi.fn()}
        onCommit={vi.fn()}
        onRollback={vi.fn()}
      />,
    );
    expect(html).toContain("cruise_v3_clean");
    expect(html).toContain("准备");
    expect(html).toContain("下载模板");
    expect(html).toContain("上传检查");
    expect(html).toContain("配置产品字段");
  });

  it("shows sheet row field and reason while blockers disable confirmation", async () => {
    const { DatabaseSetupView } = await import("./page");
    const html = renderToStaticMarkup(
      <DatabaseSetupView
        step={3}
        status={status}
        batch={batch}
        rows={{
          ...batch,
          page: 1,
          page_size: 50,
          total: 1,
          pages: 1,
          issues: [],
          items: [{
            id: "row-1",
            sheet: "prices",
            row: 8,
            action: "block",
            product_code: "P-1",
            port_id: 1,
            before_values: null,
            normalized_values: {},
            issues: [{ severity: "block", code: "INVALID_AMOUNT", message: "价格必须是数字", sheet: "价格记录", row: 8, field: "价格" }],
          }],
        }}
        result={null}
        busy={false}
        error={null}
        selectedFile={new File(["x"], "products.xlsx")}
        onStep={vi.fn()}
        onFile={vi.fn()}
        onDownload={vi.fn()}
        onRowsPage={vi.fn()}
        onCommit={vi.fn()}
        onRollback={vi.fn()}
      />,
    );
    expect(html).toContain("价格记录");
    expect(html).toContain("8");
    expect(html).toContain("价格必须是数字");
    expect(html).toContain("disabled");
  });

  it("renders old-left and new-right in review", async () => {
    const { DatabaseSetupView } = await import("./page");
    const html = renderToStaticMarkup(
      <DatabaseSetupView
        step={4}
        status={status}
        batch={{ ...batch, status: "ready", can_commit: true, counts: { ...batch.counts, block: 0 } }}
        rows={{
          ...batch,
          status: "ready",
          can_commit: true,
          counts: { ...batch.counts, block: 0 },
          page: 1,
          page_size: 50,
          total: 1,
          pages: 1,
          issues: [],
          items: [{ id: "row-2", sheet: "products", row: 5, action: "update", product_code: "P-1", port_id: 1, before_values: { core_values: { brand: "OLD" } }, normalized_values: { core_values: { brand: "NEW" } }, issues: [] }],
        }}
        result={null}
        busy={false}
        error={null}
        selectedFile={null}
        onStep={vi.fn()}
        onFile={vi.fn()}
        onDownload={vi.fn()}
        onRowsPage={vi.fn()}
        onCommit={vi.fn()}
        onRollback={vi.fn()}
      />,
    );
    expect(html).toContain("当前数据");
    expect(html).toContain("OLD");
    expect(html).toContain("导入后");
    expect(html).toContain("NEW");
  });
});
