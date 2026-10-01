import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/data-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/data-api")>();
  return {
    ...actual,
    listProducts: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    listCategories: vi.fn().mockResolvedValue([]),
    listSuppliers: vi.fn().mockResolvedValue([]),
    listCountries: vi.fn().mockResolvedValue([]),
    listPorts: vi.fn().mockResolvedValue([]),
  };
});

describe("existing product update route", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders the approved direct four-stage workflow without unrelated controls", async () => {
    const { default: ExistingProductUpdatePage } = await import("./page");
    const html = renderToStaticMarkup(<ExistingProductUpdatePage />);

    expect(html).toContain("已有产品更新");
    expect(html).toContain("选择产品");
    expect(html).toContain("选择操作");
    expect(html).toContain("编辑数据");
    expect(html).toContain("核对并保存");
    expect(html).not.toContain("下载与上传");
    expect(html).toContain("选择需要更新的产品");
    expect(html).not.toContain("新产品批量导入");
    expect(html).not.toContain("产品图片上传");
    expect(html).not.toContain("AI 匹配");
  });
});
