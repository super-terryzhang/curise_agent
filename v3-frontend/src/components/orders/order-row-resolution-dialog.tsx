"use client";

import { useEffect, useState } from "react";
import { Loader2, Search } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { getUser } from "@/lib/auth";
import { listProducts, type ProductItem } from "@/lib/data-api";
import {
  resolveOrderProductRow,
  type OrderIssueRow,
  type OrderRowResolveRequest,
} from "@/lib/orders-api";
import {
  buildConversionRequest,
  conversionScopeOptions,
  type ConversionScope,
} from "@/lib/unit-conversion-view";

export type RowResolutionMode = "edit_source" | "bind_product" | "record_conversion";

interface Props {
  open: boolean;
  mode: RowResolutionMode;
  orderId: number;
  row: OrderIssueRow;
  countryId: number | null;
  portId: number | null;
  onOpenChange: (open: boolean) => void;
  onResolved: () => Promise<void> | void;
}

const titles: Record<RowResolutionMode, string> = {
  edit_source: "修正 PO 商品行",
  bind_product: "关联数据库商品",
  record_conversion: "登记单位换算依据",
};

export function OrderRowResolutionDialog({
  open, mode, orderId, row, countryId, portId, onOpenChange, onResolved,
}: Props) {
  const [saving, setSaving] = useState(false);
  const [search, setSearch] = useState("");
  const [products, setProducts] = useState<ProductItem[]>([]);
  const [selectedProductId, setSelectedProductId] = useState<number | null>(null);
  const [form, setForm] = useState({
    product_code: row.product_code || "",
    product_name: row.product_name || "",
    quantity: row.quantity == null ? "" : String(row.quantity),
    unit: row.unit || "",
    unit_price: row.unit_price == null ? "" : String(row.unit_price),
    rfq_quantity: row.quantity == null ? "" : String(row.quantity),
    rfq_unit: row.matched_product?.unit || "",
    evidence: "",
    conversion_scope: "order_row" as ConversionScope,
    rule_source_quantity: "1",
    rule_target_quantity: "1",
    target_step: "",
    break_pack: "" as "" | "true" | "false",
  });

  useEffect(() => {
    if (!open) return;
    setSelectedProductId(null);
    setSearch(row.product_code || row.product_name || "");
    setForm({
      product_code: row.product_code || "",
      product_name: row.product_name || "",
      quantity: row.quantity == null ? "" : String(row.quantity),
      unit: row.unit || "",
      unit_price: row.unit_price == null ? "" : String(row.unit_price),
      rfq_quantity: row.quantity == null ? "" : String(row.quantity),
      rfq_unit: row.matched_product?.unit || "",
      evidence: "",
      conversion_scope: "order_row",
      rule_source_quantity: "1",
      rule_target_quantity: "1",
      target_step: "",
      break_pack: "",
    });
  }, [open, row]);

  useEffect(() => {
    if (!open || mode !== "bind_product") return;
    const timer = window.setTimeout(() => {
      void listProducts({
        search: search.trim() || undefined,
        country_id: countryId || undefined,
        port_id: portId || undefined,
        limit: 20,
      }).then((result) => setProducts(result.items)).catch((cause) => {
        toast.error(cause instanceof Error ? cause.message : "商品检索失败");
      });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [countryId, mode, open, portId, search]);

  const save = async () => {
    let payload: OrderRowResolveRequest;
    if (mode === "bind_product") {
      if (!selectedProductId) { toast.error("请选择一个数据库商品"); return; }
      payload = { action: "bind_product", product_id: selectedProductId };
    } else if (mode === "record_conversion") {
      try {
        payload = buildConversionRequest({
          scope: form.conversion_scope,
          sourceQuantity: row.quantity == null ? "" : String(row.quantity),
          sourceUnit: row.unit || "",
          rfqQuantity: form.rfq_quantity,
          rfqUnit: form.rfq_unit,
          evidence: form.evidence,
          ruleSourceQuantity: form.rule_source_quantity,
          ruleTargetQuantity: form.rule_target_quantity,
          targetStep: form.target_step,
          breakPack:
            form.break_pack === "" ? null : form.break_pack === "true",
        });
      } catch (cause) {
        toast.error(cause instanceof Error ? cause.message : "换算信息不完整");
        return;
      }
    } else {
      payload = {
        action: "edit_source",
        product_code: form.product_code.trim(),
        product_name: form.product_name.trim(),
        quantity: Number(form.quantity),
        unit: form.unit.trim(),
        unit_price: form.unit_price === "" ? undefined : Number(form.unit_price),
      };
    }
    setSaving(true);
    try {
      await resolveOrderProductRow(orderId, row.row_index, payload);
      await onResolved();
      onOpenChange(false);
      toast.success("商品行已保存并重新检查");
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const scopeOptions = conversionScopeOptions(getUser()?.role, {
    productCode: row.matched_product?.code || row.product_code,
    productName:
      row.matched_product?.product_name_en ||
      row.matched_product?.product_name_jp ||
      row.product_name,
    sourceUnit: row.unit,
    targetUnit: form.rfq_unit || row.matched_product?.unit,
    productUnit: row.matched_product?.unit,
    unitSize: row.matched_product?.unit_size,
    packSize: row.matched_product?.pack_size,
  });

  return (
    <Dialog open={open} onOpenChange={(value) => { if (!saving) onOpenChange(value); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{titles[mode]}</DialogTitle>
          <DialogDescription>第 {row.row_index} 行 · {row.product_name || "未命名商品"}。保存后系统自动重新匹配并更新问题。</DialogDescription>
        </DialogHeader>

        {mode === "edit_source" ? (
          <div className="grid grid-cols-2 gap-3">
            {([
              ["product_code", "商品代码"], ["product_name", "商品名称"],
              ["quantity", "数量"], ["unit", "单位"], ["unit_price", "单价"],
            ] as const).map(([key, label]) => (
              <label key={key} className={key === "product_name" ? "col-span-2 space-y-1" : "space-y-1"}>
                <span className="text-xs text-muted-foreground">{label}</span>
                <Input type={["quantity", "unit_price"].includes(key) ? "number" : "text"} value={form[key]} onChange={(event) => setForm((current) => ({ ...current, [key]: event.target.value }))} />
              </label>
            ))}
          </div>
        ) : null}

        {mode === "bind_product" ? (
          <div className="space-y-3">
            <div className="relative"><Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" /><Input className="pl-9" placeholder="搜索商品代码或名称" value={search} onChange={(event) => setSearch(event.target.value)} /></div>
            <div className="max-h-72 overflow-y-auto rounded-[3px] border">
              {products.length ? products.map((product) => (
                <button key={product.id} type="button" className={`grid w-full grid-cols-[110px_1fr_100px] gap-3 border-b px-3 py-2 text-left text-xs last:border-b-0 ${selectedProductId === product.id ? "bg-blue-50 ring-1 ring-inset ring-blue-500 dark:bg-blue-950/20" : "hover:bg-slate-50 dark:hover:bg-slate-900"}`} onClick={() => setSelectedProductId(product.id)}>
                  <span className="font-mono">{product.code || "—"}</span><span className="font-medium">{product.product_name_en || product.product_name_jp || "—"}</span><span className="text-muted-foreground">{product.unit || "—"} · {product.supplier_name || "无供应商"}</span>
                </button>
              )) : <div className="px-3 py-8 text-center text-xs text-muted-foreground">当前范围内没有找到商品</div>}
            </div>
            <p className="text-xs text-muted-foreground">只显示当前国家、港口和有效范围内的商品；系统不会跨范围强行匹配。</p>
          </div>
        ) : null}

        {mode === "record_conversion" ? (
          <div className="space-y-3">
            <div className="rounded-[3px] border bg-slate-50 px-3 py-2 text-xs dark:bg-slate-900/40">PO 原始：{row.quantity ?? "—"} {row.unit || "—"}　→　供应商报价单位：{row.matched_product?.unit || "待填写"}</div>
            <div className="grid grid-cols-2 gap-3"><label className="space-y-1"><span className="text-xs text-muted-foreground">询价数量</span><Input type="number" min="0" value={form.rfq_quantity} onChange={(event) => setForm((current) => ({ ...current, rfq_quantity: event.target.value }))} /></label><label className="space-y-1"><span className="text-xs text-muted-foreground">询价单位</span><Input value={form.rfq_unit} onChange={(event) => setForm((current) => ({ ...current, rfq_unit: event.target.value }))} /></label></div>
            <div className="space-y-1.5">
              <div className="text-xs text-muted-foreground">保存范围</div>
              <div className="divide-y rounded-[3px] border">
                {scopeOptions.map((option) => (
                  <label key={option.value} className="flex cursor-pointer gap-2.5 px-3 py-2.5 text-xs">
                    <input
                      type="radio"
                      name="conversion-scope"
                      className="mt-0.5"
                      checked={form.conversion_scope === option.value}
                      onChange={() => setForm((current) => ({ ...current, conversion_scope: option.value }))}
                    />
                    <span>
                      <span className="block font-medium text-foreground">{option.label}</span>
                      <span className="mt-0.5 block text-muted-foreground">{option.description}</span>
                    </span>
                  </label>
                ))}
              </div>
            </div>
            {form.conversion_scope !== "order_row" ? (
              <div className="space-y-3 rounded-[3px] border bg-blue-50/50 p-3 dark:bg-blue-950/10">
                <div className="text-xs font-medium">可复用换算关系</div>
                <div className="grid grid-cols-[1fr_auto_1fr] items-end gap-2">
                  <label className="space-y-1">
                    <span className="text-xs text-muted-foreground">来源数量</span>
                    <div className="flex items-center gap-2"><Input type="number" min="0" value={form.rule_source_quantity} onChange={(event) => setForm((current) => ({ ...current, rule_source_quantity: event.target.value }))} /><span className="text-xs">{row.unit || "—"}</span></div>
                  </label>
                  <span className="pb-2 text-sm">＝</span>
                  <label className="space-y-1">
                    <span className="text-xs text-muted-foreground">供应商数量</span>
                    <div className="flex items-center gap-2"><Input type="number" min="0" value={form.rule_target_quantity} onChange={(event) => setForm((current) => ({ ...current, rule_target_quantity: event.target.value }))} /><span className="text-xs">{form.rfq_unit || "—"}</span></div>
                  </label>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <label className="space-y-1"><span className="text-xs text-muted-foreground">供应商订购步长（未知可留空）</span><Input type="number" min="0" value={form.target_step} onChange={(event) => setForm((current) => ({ ...current, target_step: event.target.value }))} /></label>
                  <label className="space-y-1"><span className="text-xs text-muted-foreground">是否允许拆包</span><select className="flex h-9 w-full rounded-[3px] border border-input bg-transparent px-3 text-xs" value={form.break_pack} onChange={(event) => setForm((current) => ({ ...current, break_pack: event.target.value as "" | "true" | "false" }))}><option value="">尚未确认</option><option value="true">允许</option><option value="false">不允许</option></select></label>
                </div>
                <p className="text-xs text-blue-900 dark:text-blue-200">系统只会按上面的精确关系复用；不会根据单位数字后缀自行乘除，也不会自动取整。</p>
              </div>
            ) : null}
            <label className="space-y-1"><span className="text-xs text-muted-foreground">人工确认依据（必填）</span><Textarea placeholder="例如：供应商 2026-09-24 邮件确认按 10 箱报价" value={form.evidence} onChange={(event) => setForm((current) => ({ ...current, evidence: event.target.value }))} /></label>
            <p className="text-xs text-muted-foreground">系统只记录您确认的换算结果，不推断是否可以拆箱。</p>
          </div>
        ) : null}

        <DialogFooter><Button variant="outline" disabled={saving} onClick={() => onOpenChange(false)}>取消</Button><Button disabled={saving} onClick={() => void save()}>{saving ? <Loader2 className="animate-spin" /> : null}保存并重新检查</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
