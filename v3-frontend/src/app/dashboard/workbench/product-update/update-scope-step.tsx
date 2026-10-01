import { Info } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ExistingProductUpdateScope } from "@/lib/existing-product-update-workbook";

interface UpdateScopeStepProps {
  selectedCount: number;
  scope: ExistingProductUpdateScope;
  onScopeChange: (scope: ExistingProductUpdateScope) => void;
  onBack: () => void;
  onNext: () => void;
}

const scopeOptions: Array<{
  key: keyof ExistingProductUpdateScope;
  title: string;
  fields: string;
  note: string;
}> = [
  {
    key: "basic",
    title: "基本信息",
    fields: "产品名称、日文名称、品牌、类别、供应商、国家、港口、单位、规格、包装、原产地",
    note: "用于修正已存在产品的主数据。",
  },
  {
    key: "purchase",
    title: "采购价区间",
    fields: "采购价、币种、开始日期、结束日期",
    note: "可以新增区间，也可以更新导出文件中已有的区间。",
  },
  {
    key: "selling",
    title: "卖价区间",
    fields: "卖价、币种、开始日期、结束日期",
    note: "可以新增区间，也可以更新导出文件中已有的区间。",
  },
];

export function UpdateScopeStep({ selectedCount, scope, onScopeChange, onBack, onNext }: UpdateScopeStepProps) {
  const canContinue = scope.basic || scope.purchase || scope.selling;

  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-base font-semibold">选择更新范围</h2>
        <p className="mt-1 text-sm text-muted-foreground">已选择 {selectedCount} 个产品。只选择本次确实需要修改的内容，导出文件会保留必要的系统匹配列。</p>
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        {scopeOptions.map((option) => (
          <label
            key={option.key}
            className={`cursor-pointer rounded-md border p-4 transition-colors ${scope[option.key] ? "border-amber-500 bg-amber-50/70" : "bg-background hover:bg-muted/30"}`}
          >
            <div className="flex items-start gap-3">
              <input
                className="mt-1"
                type="checkbox"
                checked={scope[option.key]}
                onChange={(event) => onScopeChange({ ...scope, [option.key]: event.target.checked })}
              />
              <span>
                <span className="block text-sm font-semibold">{option.title}</span>
                <span className="mt-2 block text-sm leading-6 text-foreground">{option.fields}</span>
                <span className="mt-2 block text-xs leading-5 text-muted-foreground">{option.note}</span>
              </span>
            </div>
          </label>
        ))}
      </div>

      <div className="flex items-start gap-2 rounded-md border bg-muted/20 px-3 py-2 text-sm text-muted-foreground">
        <Info className="mt-0.5 size-4 shrink-0" />
        <span>产品图片请在“产品图片上传”中维护，本流程不会改动图片。</span>
      </div>

      <div className="flex justify-between border-t pt-4">
        <Button variant="outline" onClick={onBack}>上一步</Button>
        <Button disabled={!canContinue} onClick={onNext}>下一步</Button>
      </div>
    </section>
  );
}
