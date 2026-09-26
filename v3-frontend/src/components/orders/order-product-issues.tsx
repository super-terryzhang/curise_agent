"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  filterIssueRows,
  issueStatusText,
  issueSummary,
  productResolutionHref,
  resolutionLabel,
  type OrderIssueFilter,
} from "@/lib/order-issue-view";
import type { Order, OrderIssueFinding, OrderIssueRow } from "@/lib/orders-api";
import {
  OrderRowResolutionDialog,
  type RowResolutionMode,
} from "./order-row-resolution-dialog";

interface Props {
  order: Order;
  supplierNames: Map<number, string>;
  onResolved: () => Promise<void>;
  onEditOrder: () => void;
  onOpenSource: () => void;
  onRerun: () => void;
}

function legacyRows(order: Order): OrderIssueRow[] {
  return (order.match_results || []).map((result, index) => ({
    ...result,
    row_index: index + 1,
    match_status: result.match_status,
    inquiry_disposition: result.match_status === "matched" ? "included" : "excluded",
    inquiry_disposition_reason: result.match_reason,
    findings: [],
  }));
}

function toneClass(severity: OrderIssueFinding["severity"]) {
  return severity === "warning" ? "border-amber-300 bg-amber-50 dark:bg-amber-950/20" : "border-red-300 bg-red-50 dark:bg-red-950/20";
}

export function OrderProductIssues({ order, supplierNames, onResolved, onEditOrder, onOpenSource, onRerun }: Props) {
  const rows = order.issue_overview?.rows ?? legacyRows(order);
  const [filter, setFilter] = useState<OrderIssueFilter>("all");
  const [search, setSearch] = useState("");
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [resolution, setResolution] = useState<{ mode: RowResolutionMode; row: OrderIssueRow } | null>(null);

  const visible = useMemo(() => filterIssueRows(rows, filter).filter((row) => {
    const term = search.trim().toLocaleLowerCase();
    return !term || [row.product_name, row.product_code, row.matched_product?.product_name_en, row.matched_product?.code].some((value) => value?.toLocaleLowerCase().includes(term));
  }), [filter, rows, search]);

  useEffect(() => {
    if (selectedIndex != null && rows.some((row) => row.row_index === selectedIndex)) return;
    const firstIssue = rows.find((row) => row.findings.length > 0 || row.match_status !== "matched");
    setSelectedIndex(firstIssue?.row_index ?? rows[0]?.row_index ?? null);
  }, [rows, selectedIndex]);

  const selected = rows.find((row) => row.row_index === selectedIndex) || null;
  const actionFor = (finding: OrderIssueFinding) => {
    const target = finding.resolution?.target || "review";
    if (!selected) return null;
    if (target === "product_match") return <div className="flex flex-wrap gap-2"><Button size="sm" onClick={() => setResolution({ mode: "bind_product", row: selected })}>关联数据库商品</Button><Button size="sm" variant="outline" onClick={() => setResolution({ mode: "edit_source", row: selected })}>修正 PO 本行</Button></div>;
    if (target === "order_row") return <Button size="sm" onClick={() => setResolution({ mode: "edit_source", row: selected })}>修正 PO 本行</Button>;
    if (target === "unit_conversion") return <Button size="sm" onClick={() => setResolution({ mode: "record_conversion", row: selected })}>登记换算依据</Button>;
    const href = productResolutionHref(selected, target);
    if (href) return <Button asChild size="sm"><Link href={href}>{resolutionLabel(finding)}</Link></Button>;
    if (target === "order") return <Button size="sm" onClick={onEditOrder}>编辑订单信息</Button>;
    return <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={onOpenSource}>查看原始文件</Button><Button size="sm" variant="outline" onClick={onRerun}>重新检查</Button></div>;
  };

  return (
    <>
      <div className="grid items-start gap-3 xl:grid-cols-[minmax(0,1fr)_390px]">
        <section className="overflow-hidden rounded-[3px] border bg-background">
          <div className="flex min-h-10 flex-wrap items-center gap-2 border-b bg-slate-50 px-3 py-1.5 dark:bg-slate-900/50"><h2 className="mr-auto text-sm font-semibold">商品明细（{rows.length}）</h2><select aria-label="筛选商品问题" className="h-8 rounded-[3px] border bg-background px-2 text-xs" value={filter} onChange={(event) => setFilter(event.target.value as OrderIssueFilter)}><option value="all">全部</option><option value="not_matched">未匹配</option><option value="matched_with_issues">已匹配有问题</option><option value="inquiry_ready">可询价</option></select><Input aria-label="搜索商品" className="h-8 w-48 text-xs" placeholder="搜索代码或名称" value={search} onChange={(event) => setSearch(event.target.value)} /></div>
          <div className="overflow-x-auto"><table className="w-full min-w-[980px] border-collapse text-xs"><thead className="bg-slate-50 text-left text-muted-foreground dark:bg-slate-900/40"><tr>{["行", "PO 商品名称", "数量", "单位", "数据库匹配", "供应商", "问题", "询价结果"].map((label) => <th key={label} className="border-b border-r px-3 py-2 font-medium last:border-r-0">{label}</th>)}</tr></thead><tbody>{visible.length ? visible.map((row) => { const status = issueStatusText(row); const supplierId = row.matched_product?.supplier_id; const supplierName = row.matched_product?.supplier_name?.trim() || (supplierId != null ? supplierNames.get(supplierId)?.trim() : null); const supplierLabel = supplierName || (supplierId != null ? `供应商资料缺失（ID ${supplierId}）` : "—"); return <tr key={row.row_index} className={`cursor-pointer border-b last:border-b-0 ${selectedIndex === row.row_index ? "bg-blue-50/70 dark:bg-blue-950/15" : "hover:bg-slate-50/70 dark:hover:bg-slate-900/30"}`} onClick={() => setSelectedIndex(row.row_index)}><td className="border-r px-3 py-3">{row.row_index}</td><td className="max-w-[280px] border-r px-3 py-3 font-medium">{row.product_name || "—"}<div className="mt-0.5 font-mono text-[11px] font-normal text-muted-foreground">{row.product_code || "无代码"}</div></td><td className="border-r px-3 py-3">{row.quantity ?? "—"}</td><td className="border-r px-3 py-3">{row.unit || "—"}</td><td className="border-r px-3 py-3"><span className={row.match_status === "matched" ? "text-emerald-700" : "text-amber-700"}>{status.match}</span></td><td className="border-r px-3 py-3">{supplierLabel}</td><td className="max-w-[260px] border-r px-3 py-3"><span className={row.findings.length ? "text-amber-800" : "text-emerald-700"}>{issueSummary(row)}</span></td><td className="px-3 py-3"><span className={row.inquiry_disposition === "excluded" ? "text-red-700" : row.inquiry_disposition === "included_with_warning" ? "text-amber-700" : "text-emerald-700"}>{status.inquiry}</span></td></tr>; }) : <tr><td colSpan={8} className="px-3 py-10 text-center text-muted-foreground">当前筛选没有商品</td></tr>}</tbody></table></div>
        </section>

        <section className="overflow-hidden rounded-[3px] border bg-background"><div className="border-b bg-slate-50 px-3 py-2 dark:bg-slate-900/50"><h2 className="text-sm font-semibold">问题详情</h2></div>{selected ? <div><dl className="grid grid-cols-[88px_1fr] border-b text-xs"><dt className="border-r bg-slate-50 px-3 py-2 text-muted-foreground dark:bg-slate-900/40">商品行</dt><dd className="px-3 py-2">第 {selected.row_index} 行 · {selected.product_name || "—"}</dd><dt className="border-r border-t bg-slate-50 px-3 py-2 text-muted-foreground dark:bg-slate-900/40">匹配状态</dt><dd className="border-t px-3 py-2">{issueStatusText(selected).match}</dd><dt className="border-r border-t bg-slate-50 px-3 py-2 text-muted-foreground dark:bg-slate-900/40">询价结果</dt><dd className="border-t px-3 py-2">{issueStatusText(selected).inquiry}{selected.inquiry_disposition_reason ? <div className="mt-1 text-muted-foreground">{selected.inquiry_disposition_reason}</div> : null}</dd></dl><div className="space-y-3 p-3">{selected.findings.length ? selected.findings.map((finding, index) => <article key={`${finding.code}-${index}`} className={`rounded-[3px] border p-3 text-xs ${toneClass(finding.severity)}`}><div className="flex items-center gap-2"><span className="font-semibold">{finding.message || finding.code}</span><span className="ml-auto font-mono text-[10px] text-muted-foreground">{finding.code}</span></div>{finding.suggestion ? <p className="mt-2 leading-5">建议：{finding.suggestion}</p> : null}{finding.evidence && Object.keys(finding.evidence).length ? <details className="mt-2"><summary className="cursor-pointer text-muted-foreground">查看检测依据</summary><pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded bg-white/70 p-2 text-[10px] dark:bg-black/20">{JSON.stringify(finding.evidence, null, 2)}</pre></details> : null}<div className="mt-3">{actionFor(finding)}</div></article>) : <div className="py-6 text-center text-xs text-emerald-700">该商品行检查通过，无需处理。</div>}</div></div> : <div className="px-3 py-8 text-xs text-muted-foreground">选择一行查看完整问题。</div>}</section>
      </div>

      {resolution ? <OrderRowResolutionDialog open mode={resolution.mode} orderId={order.id} row={resolution.row} countryId={order.country_id} portId={order.port_id} onOpenChange={(open) => { if (!open) setResolution(null); }} onResolved={onResolved} /> : null}
    </>
  );
}
