import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ProductTable } from "./product-table";

describe("clean database product table", () => {
  it("renders one row per product with only agreed columns", () => {
    const html = renderToStaticMarkup(
      <ProductTable
        products={[{
          id: 1,
          code: "P-1",
          name: "APPLE",
          port: "大阪",
          country: "日本",
          supplier: "松武",
          unit: "KG",
          category: "青果",
          brand: "TEST",
          status: true,
          revision: 1,
          extensions: [{ label: "业务分类", type: "single_select", value: "winner" }],
        }]}
      />,
    );
    for (const value of ["P-1", "APPLE", "大阪", "松武", "KG", "青果", "TEST", "winner"])
      expect(html).toContain(value);
    expect(html).toContain("/dashboard/workbench/database-setup/products/1");
  });
});
