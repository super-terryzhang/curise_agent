import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

describe("product upload route handoff", () => {
  it("sends existing-product work to the dedicated guided workflow", async () => {
    const { default: ProductUploadPage } = await import("./page");
    const html = renderToStaticMarkup(<ProductUploadPage />);

    expect(html).toContain("更新已有产品或价格区间");
    expect(html).toContain('href="/dashboard/workbench/product-update"');
    expect(html).toContain("进入已有产品更新");
    expect(html).not.toContain('href="/dashboard/data?tab=products"');
  });
});
