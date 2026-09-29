import type { ProductItem } from "@/lib/data-api";
import { Badge } from "@/components/ui/badge";

function valueOrPlaceholder(value: string | null | undefined) {
  return value?.trim() || "未填写";
}

function dateOrPlaceholder(value: string | null | undefined) {
  return value ? value.slice(0, 10) : "未填写";
}

function BasicField({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 border-b border-slate-100 py-4">
      <dt className="mb-1 text-xs text-muted-foreground">{label}</dt>
      <dd className="break-words text-sm text-foreground">{value}</dd>
    </div>
  );
}

export function ProductBasicInfo({ product }: { product: ProductItem }) {
  const effective = product.is_effective ?? product.status ?? true;

  return (
    <section aria-labelledby="product-basic-info-heading" className="rounded-md border bg-background">
      <div className="border-b px-5 py-4">
        <h2 id="product-basic-info-heading" className="text-sm font-semibold">
          基本信息
        </h2>
      </div>
      <dl className="grid grid-cols-1 px-5 sm:grid-cols-2 lg:grid-cols-4 sm:gap-x-6">
        <BasicField label="英文品名" value={valueOrPlaceholder(product.product_name_en)} />
        <BasicField label="日文品名" value={valueOrPlaceholder(product.product_name_jp)} />
        <BasicField label="商品代码" value={valueOrPlaceholder(product.code)} />
        <BasicField label="品牌" value={valueOrPlaceholder(product.brand)} />
        <BasicField label="国家" value={valueOrPlaceholder(product.country_name)} />
        <BasicField label="港口" value={valueOrPlaceholder(product.port_name)} />
        <BasicField label="类别" value={valueOrPlaceholder(product.category_name)} />
        <BasicField label="供应商" value={valueOrPlaceholder(product.supplier_name)} />
        <BasicField label="单位" value={valueOrPlaceholder(product.unit)} />
        <BasicField label="单位规格" value={valueOrPlaceholder(product.unit_size)} />
        <BasicField label="包装规格" value={valueOrPlaceholder(product.pack_size)} />
        <BasicField label="原产地" value={valueOrPlaceholder(product.country_of_origin)} />
        <BasicField label="币种" value={valueOrPlaceholder(product.currency)} />
        <BasicField label="产品有效开始日期" value={dateOrPlaceholder(product.effective_from)} />
        <BasicField label="产品有效结束日期" value={dateOrPlaceholder(product.effective_to)} />
        <div className="min-w-0 border-b border-slate-100 py-4">
          <dt className="mb-1 text-xs text-muted-foreground">状态</dt>
          <dd>
            <Badge
              variant="secondary"
              className={effective ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}
            >
              {effective ? "有效" : "无效"}
            </Badge>
          </dd>
        </div>
      </dl>
    </section>
  );
}
