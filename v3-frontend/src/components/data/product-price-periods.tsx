"use client";

import { useEffect, useState } from "react";
import { Loader2, Plus } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  createProductPricePeriod,
  deactivateProductPricePeriod,
  listProductPricePeriods,
  updateProductPricePeriod,
  type ProductItem,
  type ProductPricePeriod,
} from "@/lib/data-api";

interface Props {
  product: ProductItem;
  canEdit: boolean;
  onClose: () => void;
  onChanged: () => void;
}

const blank = {
  price_type: "purchase" as "purchase" | "selling",
  amount: "",
  currency: "",
  effective_from: "",
  effective_to: "",
};

export function ProductPricePeriodsDialog({
  product,
  canEdit,
  onClose,
  onChanged,
}: Props) {
  const [periods, setPeriods] = useState<ProductPricePeriod[]>(product.price_periods ?? []);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState(blank);

  const reload = async () => {
    setLoading(true);
    try {
      setPeriods(await listProductPricePeriods(product.id));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "读取价格区间失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
    // product.id uniquely identifies this dialog instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [product.id]);

  const beginEdit = (period: ProductPricePeriod) => {
    setEditingId(period.id);
    setForm({
      price_type: period.price_type,
      amount: String(period.amount),
      currency: period.currency ?? "",
      effective_from: period.effective_from.slice(0, 10),
      effective_to: period.effective_to.slice(0, 10),
    });
  };

  const save = async () => {
    if (!form.amount || !form.effective_from || !form.effective_to) {
      toast.error("价格、开始日期和结束日期都必须填写");
      return;
    }
    if (form.effective_from > form.effective_to) {
      toast.error("有效开始日期不能晚于结束日期");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        amount: Number(form.amount),
        currency: form.currency.trim() || product.currency || null,
        effective_from: form.effective_from,
        effective_to: form.effective_to,
      };
      if (editingId) {
        await updateProductPricePeriod(product.id, editingId, payload);
        toast.success("价格区间已更新");
      } else {
        await createProductPricePeriod(product.id, {
          ...payload,
          price_type: form.price_type,
        });
        toast.success("价格区间已新增");
      }
      setEditingId(null);
      setForm(blank);
      await reload();
      onChanged();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存价格区间失败");
    } finally {
      setSaving(false);
    }
  };

  const deactivate = async (period: ProductPricePeriod) => {
    try {
      await deactivateProductPricePeriod(product.id, period.id);
      toast.success("价格区间已停用，历史记录仍然保留");
      await reload();
      onChanged();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "停用价格区间失败");
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>价格区间 · {product.product_name_en}</DialogTitle>
        </DialogHeader>

        <div className="rounded-md border">
          <div className="grid grid-cols-[80px_110px_1fr_70px_130px] gap-3 border-b bg-muted/40 px-3 py-2 text-xs font-medium text-muted-foreground">
            <span>类型</span><span className="text-right">价格</span><span>有效期间</span><span>状态</span><span className="text-right">操作</span>
          </div>
          {loading ? (
            <div className="flex h-24 items-center justify-center"><Loader2 className="h-4 w-4 animate-spin" /></div>
          ) : periods.length === 0 ? (
            <div className="px-3 py-8 text-center text-sm text-muted-foreground">尚未配置价格区间；旧价格仍可使用，但匹配结果会显示提醒。</div>
          ) : periods.map((period) => (
            <div key={period.id} className="grid grid-cols-[80px_110px_1fr_70px_130px] items-center gap-3 border-b px-3 py-2 text-sm last:border-b-0">
              <span>{period.price_type === "purchase" ? "采购价" : "卖价"}</span>
              <span className="text-right tabular-nums">{period.currency ? `${period.currency} ` : ""}{period.amount}</span>
              <span className="font-mono text-xs">{period.effective_from.slice(0, 10)} 至 {period.effective_to.slice(0, 10)}</span>
              <Badge variant="secondary" className={period.status ? "bg-green-100 text-green-700" : ""}>{period.status ? "有效" : "停用"}</Badge>
              <span className="flex justify-end gap-1">
                {canEdit && <Button variant="ghost" size="sm" onClick={() => beginEdit(period)}>编辑</Button>}
                {canEdit && period.status && <Button variant="ghost" size="sm" className="text-red-600" onClick={() => void deactivate(period)}>停用</Button>}
              </span>
            </div>
          ))}
        </div>

        {canEdit && (
          <div className="rounded-md border p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-medium"><Plus className="h-4 w-4" />{editingId ? "编辑价格区间" : "新增价格区间"}</div>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
              <div className="grid gap-1.5"><Label>类型</Label><Select disabled={editingId !== null} value={form.price_type} onValueChange={(value) => setForm((current) => ({ ...current, price_type: value as "purchase" | "selling" }))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="purchase">采购价</SelectItem><SelectItem value="selling">卖价</SelectItem></SelectContent></Select></div>
              <div className="grid gap-1.5"><Label>价格</Label><Input type="number" min={0} step="0.01" value={form.amount} onChange={(event) => setForm((current) => ({ ...current, amount: event.target.value }))} /></div>
              <div className="grid gap-1.5"><Label>币种</Label><Input value={form.currency} placeholder={product.currency ?? "JPY"} onChange={(event) => setForm((current) => ({ ...current, currency: event.target.value }))} /></div>
              <div className="grid gap-1.5"><Label>开始日期</Label><Input type="date" value={form.effective_from} onChange={(event) => setForm((current) => ({ ...current, effective_from: event.target.value }))} /></div>
              <div className="grid gap-1.5"><Label>结束日期</Label><Input type="date" value={form.effective_to} onChange={(event) => setForm((current) => ({ ...current, effective_to: event.target.value }))} /></div>
            </div>
            <div className="mt-3 flex justify-end gap-2">
              {editingId && <Button variant="outline" onClick={() => { setEditingId(null); setForm(blank); }}>取消编辑</Button>}
              <Button onClick={() => void save()} disabled={saving}>{saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}{editingId ? "保存修改" : "新增区间"}</Button>
            </div>
          </div>
        )}

        <DialogFooter><Button variant="outline" onClick={onClose}>关闭</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
