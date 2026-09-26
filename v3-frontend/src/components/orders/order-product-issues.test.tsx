import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { Order } from "@/lib/orders-api";
import { OrderProductIssues } from "./order-product-issues";

function matchedOrder(supplierName?: string): Order {
  return {
    id: 142,
    issue_overview: {
      schema_version: 1,
      actionable_row_count: 0,
      warning_row_count: 0,
      non_row_findings: [],
      rows: [{
        row_index: 1,
        product_code: "99PRD010588",
        product_name: "APPLE GRANNY SMITH US EXTRA FANCY 125CT/40LB",
        quantity: 150,
        unit: "KG2.2",
        unit_price: null,
        match_status: "matched",
        match_reason: "产品代码完全匹配",
        inquiry_disposition: "included",
        inquiry_disposition_reason: null,
        findings: [],
        matched_product: {
          id: 100,
          code: "99PRD010588",
          product_name_en: "APPLE GRANNY SMITH US EXTRA FANCY 125CT/40LB",
          product_name_jp: null,
          price: 100,
          contract_price: 120,
          currency: "JPY",
          supplier_id: 2,
          supplier_name: supplierName,
          category_id: null,
          pack_size: null,
          unit: "KG",
        },
      }],
    },
  } as unknown as Order;
}

function renderSupplier(order: Order, supplierNames = new Map<number, string>()): string {
  return renderToStaticMarkup(
    <OrderProductIssues
      order={order}
      supplierNames={supplierNames}
      onResolved={async () => undefined}
      onEditOrder={() => undefined}
      onOpenSource={() => undefined}
      onRerun={() => undefined}
    />,
  );
}

describe("PO product supplier display", () => {
  it("uses the current match supplier name without an inquiry supplier map", () => {
    const html = renderSupplier(matchedOrder("株式会社 松武"));

    expect(html).toContain("株式会社 松武");
    expect(html).not.toContain("供应商 #2");
  });

  it("makes missing supplier master data explicit instead of showing an opaque number", () => {
    const html = renderSupplier(matchedOrder());

    expect(html).toContain("供应商资料缺失（ID 2）");
    expect(html).not.toContain("供应商 #2");
  });

  it("keeps the inquiry supplier map as a rolling-deployment fallback", () => {
    const html = renderSupplier(matchedOrder(), new Map([[2, "旧接口供应商名称"]]));

    expect(html).toContain("旧接口供应商名称");
    expect(html).not.toContain("供应商资料缺失");
  });
});
