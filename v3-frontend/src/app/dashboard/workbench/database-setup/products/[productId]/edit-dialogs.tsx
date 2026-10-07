"use client";

import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import type { DeletionPreview, PricePeriod, ProductEditConfig, PeriodValues } from "@/lib/database-setup-api";

export function ProductEditDialog({ config, busy, error, onClose, onSave }: {
  config: ProductEditConfig; busy: boolean; error: string | null;
  onClose: () => void; onSave: (values: Record<string, unknown>) => void;
}) {
  const [values, setValues] = useState(config.values);
  const set = (key: string, value: unknown) => setValues((old) => ({ ...old, [key]: value }));
  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
      <DialogHeader><DialogTitle>编辑产品资料</DialogTitle><DialogDescription>少量修改直接保存。字段按当前配置显示，价格在下面的区间表单独维护。</DialogDescription></DialogHeader>
      <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); onSave(values); }}>
        <fieldset disabled={busy} className="grid gap-4 sm:grid-cols-2">
          {config.fields.map((field) => <label key={field.key} className="space-y-1.5 text-sm">
            <span>{field.label}{field.required ? "（必填）" : ""}</span>
            {(field.options.length && field.type !== "multi_select") || field.type === "boolean" ? <select aria-label={field.label} required={field.required} className="h-9 w-full rounded-md border bg-background px-3 text-sm" value={String(values[field.key] ?? "")} onChange={(event) => set(field.key, field.type === "boolean" ? event.target.value === "" ? "" : event.target.value === "true" : event.target.value)}>
              <option value="">请选择</option>
              {(field.type === "boolean" ? [{ value: "true", label: "是" }, { value: "false", label: "否" }] : field.options).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select> : <Input aria-label={field.label} required={field.required} type={field.type === "date" ? "date" : "text"} inputMode={field.type === "number" ? "decimal" : undefined} value={String(values[field.key] ?? "")} onChange={(event) => set(field.key, event.target.value)} />}
            {field.type === "link" && <span className="block text-xs text-muted-foreground">填写关联记录的编号；不确定时先在数据表页面查看。</span>}
            {field.type === "multi_select" && <span className="block text-xs text-muted-foreground">可选：{field.options.map((o) => o.label).join("、")}。多个选项用；分隔。</span>}
            {field.type === "datetime" && <span className="block text-xs text-muted-foreground">填写含时区的日期时间，例如 2027-01-01T09:00:00+09:00。</span>}
          </label>)}
        </fieldset>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <div className="flex justify-end gap-2"><Button type="button" variant="outline" disabled={busy} onClick={onClose}>取消</Button><Button disabled={busy}>{busy ? "正在保存…" : "保存修改"}</Button></div>
      </form>
    </DialogContent>
  </Dialog>;
}

export function PriceEditDialog({ type, period, busy, error, onClose, onSave }: {
  type: "purchase" | "selling"; period?: PricePeriod; busy: boolean; error: string | null;
  onClose: () => void; onSave: (values: PeriodValues) => void;
}) {
  const [amount, setAmount] = useState(period ? String(period.amount) : "");
  const [currency, setCurrency] = useState(period?.currency || "JPY");
  const [start, setStart] = useState(period?.effective_from || "");
  const [end, setEnd] = useState(period?.effective_to || "");
  const title = `${period ? "编辑" : "新增"}${type === "purchase" ? "采购价" : "卖价"}区间`;
  function submit(event: FormEvent) {
    event.preventDefault();
    onSave({ amount: Number(amount), currency, effective_from: start, effective_to: end });
  }
  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}><DialogContent>
    <DialogHeader><DialogTitle>{title}</DialogTitle><DialogDescription>同一类价格的日期不能重叠；采购价与卖价各自独立。</DialogDescription></DialogHeader>
    <form onSubmit={submit} className="space-y-4"><fieldset disabled={busy} className="grid grid-cols-2 gap-4">
      <label className="space-y-1.5 text-sm">价格<Input aria-label="价格" required type="number" min="0" max="99999999.99" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} /></label>
      <label className="space-y-1.5 text-sm">币种<Input aria-label="币种" required pattern="[A-Z]{3}" maxLength={3} value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} /></label>
      <label className="space-y-1.5 text-sm">开始日期<Input aria-label="开始日期" required type="date" value={start} max={end || undefined} onChange={(e) => setStart(e.target.value)} /></label>
      <label className="space-y-1.5 text-sm">结束日期<Input aria-label="结束日期" required type="date" value={end} min={start || undefined} onChange={(e) => setEnd(e.target.value)} /></label>
    </fieldset>{error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <div className="flex justify-end gap-2"><Button type="button" variant="outline" disabled={busy} onClick={onClose}>取消</Button><Button disabled={busy}>{busy ? "正在保存…" : "保存区间"}</Button></div></form>
  </DialogContent></Dialog>;
}

export function DeleteDialog({ preview, period, busy, error, onClose, onDelete }: {
  preview?: DeletionPreview; period?: PricePeriod; busy: boolean; error: string | null;
  onClose: () => void; onDelete: (code: string) => void;
}) {
  const [code, setCode] = useState("");
  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}><DialogContent>
    <DialogHeader><DialogTitle>{period ? "删除价格区间" : "删除产品"}</DialogTitle><DialogDescription>这是永久删除，不能通过恢复按钮找回；操作历史仍保留。</DialogDescription></DialogHeader>
    {period ? <p className="text-sm">将删除 {period.effective_from} 至 {period.effective_to} 的{period.price_type === "purchase" ? "采购价" : "卖价"} {period.amount} {period.currency}，其他区间不变。</p> : preview && <>
      <p className="text-sm">将删除产品 {preview.code} 及其全部 {preview.period_count} 条价格区间。</p>
      {preview.can_delete ? <label className="space-y-2 text-sm">请输入产品代码 {preview.code} 确认<Input aria-label="确认产品代码" disabled={busy} value={code} onChange={(event) => setCode(event.target.value)} /></label> : <p role="alert" className="text-sm text-destructive">{preview.reasons.join("；")}</p>}
    </>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <div className="flex justify-end gap-2"><Button variant="outline" disabled={busy} onClick={onClose}>取消</Button><Button variant="destructive" disabled={busy || (!period && (!preview?.can_delete || code !== preview.code))} onClick={() => onDelete(code)}>{busy ? "正在删除…" : "确认永久删除"}</Button></div>
  </DialogContent></Dialog>;
}
