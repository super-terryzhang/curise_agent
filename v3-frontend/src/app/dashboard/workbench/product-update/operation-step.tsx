import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ProductItem } from "@/lib/data-api";
import {
  matchPeriodTargets,
  scopeLabel,
  type EditOperation,
  type EditScope,
} from "@/lib/product-batch-edit";

interface Props {
  products: ProductItem[];
  scope: EditScope;
  operation: EditOperation;
  from: string;
  to: string;
  onChange: (
    scope: EditScope,
    operation: EditOperation,
    from: string,
    to: string,
  ) => void;
  onBack: () => void;
  onNext: () => void;
}
export function OperationStep({
  products,
  scope,
  operation,
  from,
  to,
  onChange,
  onBack,
  onNext,
}: Props) {
  const matching =
    scope === "basic" ? null : matchPeriodTargets(products, scope, from, to);
  const canContinue =
    scope === "basic" ||
    operation === "add" ||
    Boolean(from && to && matching?.targets.length);
  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-base font-semibold">选择本次更新内容</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          已选择 {products.length}{" "}
          个产品。每次只处理一种内容，采购价和卖价各自独立。
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        {(["basic", "purchase", "selling"] as const).map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={scope === value}
            onClick={() =>
              onChange(value, value === "basic" ? "edit" : operation, from, to)
            }
            className={`rounded-md border p-4 text-left text-sm ${scope === value ? "border-amber-500 bg-amber-50 text-amber-950" : "bg-muted/10"}`}
          >
            <div className="font-medium">{scopeLabel(value)}</div>
            <div className="mt-2 text-xs text-muted-foreground">
              {value === "basic"
                ? "名称、代码、供应商、单位及其他现有信息"
                : "价格、币种、开始日期和结束日期"}
            </div>
          </button>
        ))}
      </div>
      {scope !== "basic" && (
        <div className="space-y-4 rounded-md border p-4">
          <div className="flex gap-5 text-sm">
            {(["edit", "add"] as const).map((value) => (
              <label key={value} className="flex items-center gap-2">
                <input
                  type="radio"
                  name="period-operation"
                  checked={operation === value}
                  onChange={() => onChange(scope, value, from, to)}
                />
                {value === "edit" ? "修改已有区间" : "新增价格区间"}
              </label>
            ))}
          </div>
          {operation === "edit" ? (
            <>
              <p className="text-sm text-muted-foreground">
                用原来的开始日期和结束日期定位区间，下一步再填写新日期或价格。
              </p>
              <div className="flex flex-wrap gap-4">
                <label className="space-y-1 text-sm">
                  原开始日期
                  <Input
                    type="date"
                    aria-label="原开始日期"
                    value={from}
                    onChange={(e) =>
                      onChange(scope, operation, e.target.value, to)
                    }
                  />
                </label>
                <label className="space-y-1 text-sm">
                  原结束日期
                  <Input
                    type="date"
                    aria-label="原结束日期"
                    value={to}
                    onChange={(e) =>
                      onChange(scope, operation, from, e.target.value)
                    }
                  />
                </label>
              </div>
              <div className="rounded-md border bg-muted/20 p-3 text-sm">
                找到 {matching?.targets.length ?? 0} 个区间；
                {matching?.missing.length ?? 0} 个产品没有对应区间。
                <p className="mt-1 text-muted-foreground">
                  未找到区间的产品不会自动新增，也不会在本次更新中修改。
                </p>
                {Boolean(from && to && matching?.missing.length) && (
                  <details className="mt-2">
                    <summary className="cursor-pointer">
                      查看未找到区间的产品
                    </summary>
                    <ul className="mt-2 max-h-40 overflow-auto pl-4">
                      {matching?.missing.map((p) => (
                        <li key={p.id}>
                          {p.code || "—"} · {p.product_name_en}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              为所选产品填写新的价格区间；已有区间保持不变。每个产品的价格需要填写，不自动复制旧价格。
            </p>
          )}
        </div>
      )}
      <div className="flex justify-between border-t pt-4">
        <Button variant="outline" onClick={onBack}>
          上一步
        </Button>
        <Button disabled={!canContinue} onClick={onNext}>
          下一步
        </Button>
      </div>
    </section>
  );
}
