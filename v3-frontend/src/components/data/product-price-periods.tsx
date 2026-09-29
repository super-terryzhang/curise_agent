"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, Plus } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  createProductPricePeriod,
  deactivateProductPricePeriod,
  listProductPricePeriods,
  updateProductPricePeriod,
  type ProductItem,
  type ProductPricePeriod,
} from "@/lib/data-api";

interface PanelProps { product: ProductItem; canEdit: boolean; onChanged: () => void }
interface DialogProps extends PanelProps { onClose: () => void }

const blank = {
  price_type: "purchase" as "purchase" | "selling",
  amount: "",
  currency: "",
  effective_from: "",
  effective_to: "",
};

export function ProductPricePeriodGroups({ periods, canEdit, onEdit, onDeactivate }: {
  periods: ProductPricePeriod[];
  canEdit: boolean;
  onEdit: (period: ProductPricePeriod) => void;
  onDeactivate: (period: ProductPricePeriod) => void;
}) {
  return <div className="grid gap-4 xl:grid-cols-2">
    <PricePeriodGroup title="采购价区间" emptyText="尚未配置采购价区间；系统会继续使用旧采购价并显示提醒。"
      periods={periods.filter(period => period.price_type === "purchase")} canEdit={canEdit} onEdit={onEdit} onDeactivate={onDeactivate} />
    <PricePeriodGroup title="卖价区间" emptyText="尚未配置卖价区间；系统会继续使用旧卖价并显示提醒。"
      periods={periods.filter(period => period.price_type === "selling")} canEdit={canEdit} onEdit={onEdit} onDeactivate={onDeactivate} />
  </div>;
}

function PricePeriodGroup({ title, emptyText, periods, canEdit, onEdit, onDeactivate }: {
  title: string;
  emptyText: string;
  periods: ProductPricePeriod[];
  canEdit: boolean;
  onEdit: (period: ProductPricePeriod) => void;
  onDeactivate: (period: ProductPricePeriod) => void;
}) {
  return <section className="rounded-md border">
    <h3 className="border-b bg-muted/30 px-4 py-3 text-sm font-medium">{title}</h3>
    {periods.length === 0 ? <p className="px-4 py-8 text-center text-sm text-muted-foreground">{emptyText}</p> : <div className="divide-y">
      {periods.map(period => <div key={period.id} className="grid gap-2 px-4 py-3 text-sm sm:grid-cols-[100px_1fr_auto] sm:items-center">
        <span className="tabular-nums">{period.currency ? `${period.currency} ` : ""}{period.amount}</span>
        <div><div className="font-mono text-xs">{period.effective_from.slice(0, 10)} 至 {period.effective_to.slice(0, 10)}</div>
          <Badge variant="secondary" className={period.status ? "mt-1 bg-green-100 text-green-700" : "mt-1"}>{period.status ? "有效" : "停用"}</Badge></div>
        {canEdit && <div className="flex justify-end gap-1"><Button variant="ghost" size="sm" onClick={() => onEdit(period)}>编辑</Button>
          {period.status && <Button variant="ghost" size="sm" className="text-red-600" onClick={() => onDeactivate(period)}>停用</Button>}</div>}
      </div>)}
    </div>}
  </section>;
}

export function ProductPricePeriodsPanel({ product, canEdit, onChanged }: PanelProps) {
  const [periods, setPeriods] = useState<ProductPricePeriod[]>(product.price_periods ?? []);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState(blank);

  const reload = useCallback(async () => {
    setLoading(true); setError("");
    try { setPeriods(await listProductPricePeriods(product.id)); }
    catch (loadError) { setError(loadError instanceof Error ? loadError.message : "读取价格区间失败"); }
    finally { setLoading(false); }
  }, [product.id]);

  useEffect(() => { void reload(); }, [reload]);

  function beginEdit(period: ProductPricePeriod) {
    setEditingId(period.id);
    setForm({ price_type: period.price_type, amount: String(period.amount), currency: period.currency ?? "",
      effective_from: period.effective_from.slice(0, 10), effective_to: period.effective_to.slice(0, 10) });
  }

  async function save() {
    if (!form.amount || !form.effective_from || !form.effective_to) { toast.error("价格、开始日期和结束日期都必须填写"); return; }
    if (form.effective_from > form.effective_to) { toast.error("有效开始日期不能晚于结束日期"); return; }
    setSaving(true);
    try {
      const payload = { amount: Number(form.amount), currency: form.currency.trim() || product.currency || null,
        effective_from: form.effective_from, effective_to: form.effective_to };
      if (editingId) { await updateProductPricePeriod(product.id, editingId, payload); toast.success("价格区间已更新"); }
      else { await createProductPricePeriod(product.id, { ...payload, price_type: form.price_type }); toast.success("价格区间已新增"); }
      setEditingId(null); setForm(blank); await reload(); onChanged();
    } catch (saveError) { toast.error(saveError instanceof Error ? saveError.message : "保存价格区间失败"); }
    finally { setSaving(false); }
  }

  async function deactivate(period: ProductPricePeriod) {
    try { await deactivateProductPricePeriod(product.id, period.id); toast.success("价格区间已停用，历史记录仍然保留"); await reload(); onChanged(); }
    catch (deactivateError) { toast.error(deactivateError instanceof Error ? deactivateError.message : "停用价格区间失败"); }
  }

  return <section aria-labelledby="price-periods-heading" className="space-y-4 rounded-md border bg-background p-5">
    <div><h2 id="price-periods-heading" className="text-sm font-semibold">价格区间</h2>
      <p className="mt-1 text-xs text-muted-foreground">采购价与卖价分别维护；同一类型的有效日期不能重叠。</p></div>
    {loading ? <div role="status" className="flex h-24 items-center justify-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在加载价格区间…</div>
      : error ? <div role="alert" className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}，请<Button variant="link" className="h-auto px-1 text-red-700" onClick={() => void reload()}>重新加载</Button>。</div>
      : <ProductPricePeriodGroups periods={periods} canEdit={canEdit} onEdit={beginEdit} onDeactivate={period => void deactivate(period)} />}

    {canEdit && <div className="rounded-md border p-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium"><Plus className="h-4 w-4" />{editingId ? "编辑价格区间" : "新增价格区间"}</div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <div className="grid gap-1.5"><Label>类型</Label><Select disabled={editingId !== null} value={form.price_type} onValueChange={value => setForm(current => ({ ...current, price_type: value as "purchase" | "selling" }))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="purchase">采购价</SelectItem><SelectItem value="selling">卖价</SelectItem></SelectContent></Select></div>
        <div className="grid gap-1.5"><Label>价格</Label><Input type="number" min={0} step="0.01" value={form.amount} onChange={event => setForm(current => ({ ...current, amount: event.target.value }))} /></div>
        <div className="grid gap-1.5"><Label>币种</Label><Input value={form.currency} placeholder={product.currency ?? "JPY"} onChange={event => setForm(current => ({ ...current, currency: event.target.value }))} /></div>
        <div className="grid gap-1.5"><Label>开始日期</Label><Input type="date" value={form.effective_from} onChange={event => setForm(current => ({ ...current, effective_from: event.target.value }))} /></div>
        <div className="grid gap-1.5"><Label>结束日期</Label><Input type="date" value={form.effective_to} onChange={event => setForm(current => ({ ...current, effective_to: event.target.value }))} /></div>
      </div>
      <div className="mt-3 flex justify-end gap-2">{editingId && <Button variant="outline" onClick={() => { setEditingId(null); setForm(blank); }}>取消编辑</Button>}
        <Button onClick={() => void save()} disabled={saving}>{saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}{editingId ? "保存修改" : "新增区间"}</Button></div>
    </div>}
  </section>;
}

export function ProductPricePeriodsDialog({ product, canEdit, onClose, onChanged }: DialogProps) {
  return <Dialog open onOpenChange={open => !open && onClose()}><DialogContent className="max-h-[85vh] max-w-5xl overflow-y-auto">
    <DialogHeader><DialogTitle>价格区间 · {product.product_name_en}</DialogTitle></DialogHeader>
    <ProductPricePeriodsPanel product={product} canEdit={canEdit} onChanged={onChanged} />
    <DialogFooter><Button variant="outline" onClick={onClose}>关闭</Button></DialogFooter>
  </DialogContent></Dialog>;
}
