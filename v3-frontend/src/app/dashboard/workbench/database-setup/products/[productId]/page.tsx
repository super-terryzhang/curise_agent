"use client";

import { useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { ProductEditDialog, PriceEditDialog, DeleteDialog } from "./edit-dialogs";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PREPARATION_PATH } from "@/lib/data-preparation-routes";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  getSetupProduct,
  type PricePeriod,
  type SetupProductDetail,
  type ProductEditConfig,
  type DeletionPreview,
  getProductEditConfig, saveSetupProduct, previewSetupProductDelete,
  deleteSetupProduct, saveSetupPeriod, deleteSetupPeriod,
} from "@/lib/database-setup-api";

function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.join("、");
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value);
}

function periodState(period: PricePeriod, today: string): string {
  if (!period.status) return "停用";
  if (period.effective_to < today) return "已结束";
  if (period.effective_from > today) return "未来";
  return "当前";
}

function PeriodTable({
  title,
  periods,
  today,
  onAdd,
  onEdit,
  onDelete,
}: {
  title: string;
  periods: PricePeriod[];
  today: string;
  onAdd?: () => void;
  onEdit?: (period: PricePeriod) => void;
  onDelete?: (period: PricePeriod) => void;
}) {
  const ordered = [...periods].sort((a, b) =>
    a.effective_from.localeCompare(b.effective_from),
  );
  return (
    <Card className="gap-0 overflow-hidden rounded-md py-0 shadow-sm">
      <CardHeader className="flex flex-row items-center justify-between border-b px-5 py-4"><CardTitle className="text-sm">{title}</CardTitle><Button size="sm" variant="outline" onClick={onAdd}>新增{title}</Button></CardHeader>
      <Table>
        <TableHeader className="bg-muted/40"><TableRow><TableHead>价格</TableHead><TableHead>币种</TableHead><TableHead>开始日期</TableHead><TableHead>结束日期</TableHead><TableHead>状态</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader>
        <TableBody>
          {ordered.map((period) => <TableRow key={period.id}><TableCell className="font-medium tabular-nums">{period.amount}</TableCell><TableCell>{display(period.currency)}</TableCell><TableCell>{period.effective_from}</TableCell><TableCell>{period.effective_to}</TableCell><TableCell><Badge variant="outline">{periodState(period, today)}</Badge></TableCell><TableCell className="space-x-2 text-right"><Button size="sm" variant="ghost" onClick={() => onEdit?.(period)}>编辑</Button><Button size="sm" variant="ghost" className="text-destructive" onClick={() => onDelete?.(period)}>删除区间</Button></TableCell></TableRow>)}
          {!ordered.length && <TableRow><TableCell colSpan={6} className="h-20 text-center text-muted-foreground">未配置</TableCell></TableRow>}
        </TableBody>
      </Table>
    </Card>
  );
}

export function ProductDetailView({
  product,
  today,
  onEditProduct,
  onAddPeriod,
  onEditPeriod,
  onDeletePeriod,
}: {
  product: SetupProductDetail;
  today: string;
  onEditProduct?: () => void;
  onAddPeriod?: (type: "purchase" | "selling") => void;
  onEditPeriod?: (period: PricePeriod) => void;
  onDeletePeriod?: (period: PricePeriod) => void;
}) {
  const facts = [
    ["产品代码", product.code], ["港口", product.port], ["产品名称", product.name],
    ["供应商", product.supplier], ["单位", product.unit], ["商品分类", product.category],
    ["品牌", product.brand], ["国家", product.country], ["状态", product.status ? "启用" : "停用"],
    ...product.extensions.map((item) => [item.label, item.value] as [string, unknown]),
  ];
  return (
    <div className="space-y-5">
      <Card className="gap-0 rounded-md py-0 shadow-sm">
        <CardHeader className="flex flex-row items-center justify-between border-b px-5 py-4"><CardTitle className="text-sm">产品资料</CardTitle><Button variant="outline" size="sm" onClick={onEditProduct}>编辑产品资料</Button></CardHeader>
        <CardContent className="grid gap-px bg-border p-0 sm:grid-cols-3">
          {facts.map(([label, value]) => <div key={String(label)} className="bg-background px-5 py-3"><div className="text-[11px] text-muted-foreground">{label}</div><div className="mt-1 text-sm">{display(value)}</div></div>)}
        </CardContent>
      </Card>
      <PeriodTable title="采购价区间" today={today} periods={product.price_periods.filter((period) => period.price_type === "purchase")} onAdd={() => onAddPeriod?.("purchase")} onEdit={onEditPeriod} onDelete={onDeletePeriod} />
      <PeriodTable title="卖价区间" today={today} periods={product.price_periods.filter((period) => period.price_type === "selling")} onAdd={() => onAddPeriod?.("selling")} onEdit={onEditPeriod} onDelete={onDeletePeriod} />
    </div>
  );
}

export default function DatabaseSetupProductPage() {
  const params = useParams<{ productId: string }>();
  const router = useRouter();
  const [product, setProduct] = useState<SetupProductDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editConfig, setEditConfig] = useState<ProductEditConfig | null>(null);
  const [priceEdit, setPriceEdit] = useState<{ type: "purchase" | "selling"; period?: PricePeriod } | null>(null);
  const [deletion, setDeletion] = useState<DeletionPreview | null>(null);
  const [deletePeriod, setDeletePeriod] = useState<PricePeriod | null>(null);
  const id = Number(params.productId);
  function close() { setEditConfig(null); setPriceEdit(null); setDeletion(null); setDeletePeriod(null); setDialogError(null); }
  async function openProductEdit() {
    setBusy(true); setError(null); setNotice(null);
    try { setEditConfig(await getProductEditConfig(id)); setDialogError(null); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "读取编辑资料失败"); }
    finally { setBusy(false); }
  }
  async function openDelete() {
    setBusy(true); setError(null); setNotice(null);
    try { setDeletion(await previewSetupProductDelete(id)); setDialogError(null); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "读取删除范围失败"); }
    finally { setBusy(false); }
  }
  async function mutate(operation: () => Promise<unknown>, deletingProduct = false) {
    setBusy(true); setDialogError(null);
    try {
      await operation();
      if (deletingProduct) { router.push(PREPARATION_PATH); return; }
      setProduct(await getSetupProduct(id)); close(); setNotice("已保存，页面数据已刷新。");
    } catch (reason) { setDialogError(reason instanceof Error ? reason.message : "操作失败，请刷新核对后重试"); }
    finally { setBusy(false); }
  }
  useEffect(() => {
    let active = true;
    getSetupProduct(Number(params.productId))
      .then((value) => { if (active) setProduct(value); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "读取产品失败"); });
    return () => { active = false; };
  }, [params.productId]);
  return (
    <div className="h-full overflow-y-auto bg-muted/20">
      <div className="mx-auto max-w-6xl px-6 py-6">
        <Link href={PREPARATION_PATH} className="mb-2 flex items-center gap-1 text-xs text-muted-foreground"><ArrowLeft className="h-3.5 w-3.5" />返回产品数据</Link>
        <div className="mb-4 flex items-center justify-between"><div><h1 className="text-lg font-semibold">{product?.name || "产品详情"}</h1><p className="mt-1 text-xs text-muted-foreground">产品资料直接编辑；采购价和卖价按期间逐行维护。</p></div>{product && <Button size="sm" variant="outline" className="text-destructive" disabled={busy} onClick={() => void openDelete()}>删除产品</Button>}</div>
        {product && error && <p role="alert" className="mb-3 text-sm text-destructive">{error}</p>}
        {notice && <p role="status" className="mb-3 text-sm text-muted-foreground">{notice}</p>}
        {product ? <fieldset disabled={busy}><ProductDetailView product={product} today={new Date().toISOString().slice(0, 10)} onEditProduct={() => void openProductEdit()} onAddPeriod={(type) => { close(); setPriceEdit({ type }); }} onEditPeriod={(period) => { close(); setPriceEdit({ type: period.price_type, period }); }} onDeletePeriod={(period) => { close(); setDeletePeriod(period); }} /></fieldset> : <Card><CardContent className="flex h-40 items-center justify-center text-sm text-muted-foreground">{error || "正在读取产品…"}</CardContent></Card>}
        {editConfig && <ProductEditDialog config={editConfig} busy={busy} error={dialogError} onClose={close} onSave={(values) => void mutate(() => saveSetupProduct(id, editConfig, values))} />}
        {priceEdit && <PriceEditDialog type={priceEdit.type} period={priceEdit.period} busy={busy} error={dialogError} onClose={close} onSave={(values) => void mutate(() => saveSetupPeriod(id, priceEdit.type, values, priceEdit.period))} />}
        {(deletion || deletePeriod) && <DeleteDialog preview={deletion || undefined} period={deletePeriod || undefined} busy={busy} error={dialogError} onClose={close} onDelete={(code) => void mutate(() => deletePeriod ? deleteSetupPeriod(id, deletePeriod) : deleteSetupProduct(id, deletion!, code), !deletePeriod)} />}
      </div>
    </div>
  );
}
