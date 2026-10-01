import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { ProductItem } from "@/lib/data-api";
import { ProductSelectionStep } from "./product-selection-step";
import { UpdateScopeStep } from "./update-scope-step";
import { WorkbookStep } from "./workbook-step";

const product: ProductItem = {
  id: 18,
  revision: 4,
  price_version: 2,
  product_name_en: "APPLE GRANNY SMITH",
  product_name_jp: "青りんご",
  code: "001258",
  unit: "KG",
  price: 320,
  contract_price: 410,
  profit_margin: 21.95,
  thumbnail_url: null,
  image_count: 0,
  unit_size: "1 KG",
  pack_size: "10 KG",
  country_of_origin: "USA",
  brand: "ORCHARD",
  currency: "JPY",
  status: true,
  country_name: "日本",
  category_name: "青果",
  supplier_name: "タカナシ販売株式会社",
  port_name: "大阪",
  country_id: 1,
  category_id: 2,
  supplier_id: 3,
  port_id: 4,
};

describe("existing product update setup steps", () => {
  it("renders the real product filters, current-page controls and product columns", () => {
    const html = renderToStaticMarkup(
      <ProductSelectionStep
        products={[product]}
        total={1}
        page={0}
        pageSize={20}
        selectedProducts={{}}
        filters={{ search: "", category: "all", supplier: "all", country: "all", port: "all", status: "all" }}
        categories={[{ id: 2, name: "青果", code: null, description: null, status: true }]}
        suppliers={[{ id: 3, name: "タカナシ販売株式会社", contact: null, email: null, phone: null, address: null, zip_code: null, fax: null, default_payment_method: null, default_payment_terms: null, status: true, country_name: "日本", country_id: 1, categories: [], category_ids: [] }]}
        countries={[{ id: 1, name: "日本", code: "JP", status: true }]}
        ports={[{ id: 4, name: "大阪", code: "OSA", location: null, status: true, country_name: "日本", country_id: 1 }]}
        loading={false}
        error={null}
        onFiltersChange={vi.fn()}
        onSelectProduct={vi.fn()}
        onSelectVisible={vi.fn()}
        onClearSelection={vi.fn()}
        onPageChange={vi.fn()}
        onRetry={vi.fn()}
        onNext={vi.fn()}
      />,
    );

    expect(html).toContain("搜索产品名或代码");
    expect(html).toContain("全部类别");
    expect(html).toContain("全部供应商");
    expect(html).toContain("全部国家");
    expect(html).toContain("全部港口");
    expect(html).toContain("全部状态");
    expect(html).toContain("选择当前页");
    expect(html).toContain("产品代码");
    expect(html).toContain("产品名称");
    expect(html).toContain("供应商");
    expect(html).toContain("国家 / 港口");
    expect(html).toContain("单位");
    expect(html).toContain("001258");
    expect(html).toContain("APPLE GRANNY SMITH");
    expect(html).toContain("已选择 0 个产品");
    expect(html).toMatch(/<button[^>]*disabled[^>]*>下一步/);
    expect(html).not.toContain("AI");
    expect(html).not.toContain("产品图片");
  });

  it("renders only the three approved update scopes and blocks an empty scope", () => {
    const html = renderToStaticMarkup(
      <UpdateScopeStep
        selectedCount={1}
        scope={{ basic: false, purchase: false, selling: false }}
        onScopeChange={vi.fn()}
        onBack={vi.fn()}
        onNext={vi.fn()}
      />,
    );

    expect(html).toContain("基本信息");
    expect(html).toContain("产品名称、日文名称、品牌、类别、供应商、国家、港口、单位、规格、包装、原产地");
    expect(html).toContain("采购价区间");
    expect(html).toContain("采购价、币种、开始日期、结束日期");
    expect(html).toContain("卖价区间");
    expect(html).toContain("卖价、币种、开始日期、结束日期");
    expect(html).toContain("产品图片请在“产品图片上传”中维护");
    expect(html).toMatch(/<button[^>]*disabled[^>]*>下一步/);
    expect(html).not.toContain("自动定价");
    expect(html).not.toContain("审批");
  });

  it("keeps download and re-upload together and explains the hidden matching columns", () => {
    const html = renderToStaticMarkup(
      <WorkbookStep
        selectedCount={3}
        scope={{ basic: true, purchase: true, selling: false }}
        fileName={null}
        downloading={false}
        validating={false}
        error={null}
        onDownload={vi.fn()}
        onFileChange={vi.fn()}
        onBack={vi.fn()}
        onValidate={vi.fn()}
      />,
    );

    expect(html).toContain("下载更新文件");
    expect(html).toContain("重新上传已修改文件");
    expect(html).toContain("已有产品更新_YYYY-MM-DD.xlsx");
    expect(html).toContain("系统匹配列已隐藏，请勿删除、改名或修改");
    expect(html).toContain("仅支持 .xlsx");
    expect(html).toMatch(/<button[^>]*disabled[^>]*>开始程序检查/);
    expect(html).not.toContain("拖入整个文件夹");
    expect(html).not.toContain("图片");
  });
});
