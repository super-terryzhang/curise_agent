"use client";

import { useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
}: {
  title: string;
  periods: PricePeriod[];
  today: string;
}) {
  const ordered = [...periods].sort((a, b) =>
    a.effective_from.localeCompare(b.effective_from),
  );
  return (
    <Card className="gap-0 overflow-hidden rounded-md py-0 shadow-sm">
      <CardHeader className="border-b px-5 py-4"><CardTitle className="text-sm">{title}</CardTitle></CardHeader>
      <Table>
        <TableHeader className="bg-muted/40"><TableRow><TableHead>价格</TableHead><TableHead>币种</TableHead><TableHead>开始日期</TableHead><TableHead>结束日期</TableHead><TableHead>状态</TableHead></TableRow></TableHeader>
        <TableBody>
          {ordered.map((period) => <TableRow key={period.id}><TableCell className="font-medium tabular-nums">{period.amount}</TableCell><TableCell>{display(period.currency)}</TableCell><TableCell>{period.effective_from}</TableCell><TableCell>{period.effective_to}</TableCell><TableCell><Badge variant="outline">{periodState(period, today)}</Badge></TableCell></TableRow>)}
          {!ordered.length && <TableRow><TableCell colSpan={5} className="h-20 text-center text-muted-foreground">未配置</TableCell></TableRow>}
        </TableBody>
      </Table>
    </Card>
  );
}

export function ProductDetailView({
  product,
  today,
}: {
  product: SetupProductDetail;
  today: string;
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
        <CardHeader className="border-b px-5 py-4"><CardTitle className="text-sm">产品资料</CardTitle></CardHeader>
        <CardContent className="grid gap-px bg-border p-0 sm:grid-cols-3">
          {facts.map(([label, value]) => <div key={String(label)} className="bg-background px-5 py-3"><div className="text-[11px] text-muted-foreground">{label}</div><div className="mt-1 text-sm">{display(value)}</div></div>)}
        </CardContent>
      </Card>
      <PeriodTable title="采购价区间" today={today} periods={product.price_periods.filter((period) => period.price_type === "purchase")} />
      <PeriodTable title="卖价区间" today={today} periods={product.price_periods.filter((period) => period.price_type === "selling")} />
    </div>
  );
}

export default function DatabaseSetupProductPage() {
  const params = useParams<{ productId: string }>();
  const [product, setProduct] = useState<SetupProductDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
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
        <Link href="/dashboard/workbench/database-setup" className="mb-2 flex items-center gap-1 text-xs text-muted-foreground"><ArrowLeft className="h-3.5 w-3.5" />返回新数据库产品准备</Link>
        <div className="mb-4"><h1 className="text-lg font-semibold">{product?.name || "产品详情"}</h1><p className="mt-1 text-xs text-muted-foreground">一个产品对应多条互不重叠的采购价和卖价期间。</p></div>
        {product ? <ProductDetailView product={product} today={new Date().toISOString().slice(0, 10)} /> : <Card><CardContent className="flex h-40 items-center justify-center text-sm text-muted-foreground">{error || "正在读取产品…"}</CardContent></Card>}
      </div>
    </div>
  );
}
