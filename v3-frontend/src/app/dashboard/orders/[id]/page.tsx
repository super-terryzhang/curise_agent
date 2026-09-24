"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ChevronDown, Download, FileText, Loader2, MoreHorizontal, Plus, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  downloadOrderFile,
  getOrder,
  getOrderFilePreview,
  getPortsList,
  rematchOrder,
  reprocessOrder,
  reviewOrder,
  runAnomalyCheck,
  updateOrder,
  type MatchResult,
  type Order,
  type OrderProduct,
  type PortItem,
} from "@/lib/orders-api";
import { getArrangementWorkspace, type ArrangementWorkspace } from "@/lib/order-groups-api";
import { formatBusinessDateTime, orderDetailStatus, type BusinessTone } from "@/lib/order-workspace-view";

type Tab = "products" | "info" | "source" | "history";
type ResultFilter = "all" | "matched" | "not_matched";

const toneClass: Record<BusinessTone, string> = {
  neutral: "bg-slate-400",
  success: "bg-emerald-600",
  warning: "bg-amber-500",
  danger: "bg-red-600",
  progress: "bg-blue-600",
};

function StatusText({ label, tone }: { label: string; tone: BusinessTone }) {
  return <span className="inline-flex items-center gap-2 whitespace-nowrap text-xs"><span className={`h-2 w-2 rounded-full ${toneClass[tone]}`} />{label}</span>;
}

function Panel({ title, aside, children, className = "" }: { title: string; aside?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return <section className={`overflow-hidden rounded-[3px] border bg-background ${className}`}><div className="flex min-h-9 items-center gap-3 border-b bg-slate-50 px-3 py-1.5 dark:bg-slate-900/50"><h2 className="text-sm font-semibold">{title}</h2>{aside ? <div className="ml-auto">{aside}</div> : null}</div>{children}</section>;
}

function rowFinding(order: Order, result: MatchResult, index: number) {
  return (order.anomaly_data?.findings || []).find((item) => {
    const row = item.row_index ?? item.source_line;
    return Number(row) === index + 1 || (!!item.product_name && item.product_name === result.product_name);
  });
}

function supplierName(result: MatchResult, workspace: ArrangementWorkspace | null) {
  const supplierId = result.matched_product?.supplier_id;
  if (supplierId == null) return "—";
  return workspace?.suppliers.find((item) => item.supplier_id === supplierId)?.supplier_name || `供应商 #${supplierId}`;
}

export default function OrderDetailPage() {
  const params = useParams();
  const router = useRouter();
  const orderId = Number(params.id);
  const [order, setOrder] = useState<Order | null>(null);
  const [workspace, setWorkspace] = useState<ArrangementWorkspace | null>(null);
  const [ports, setPorts] = useState<PortItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [activeTab, setActiveTab] = useState<Tab>("products");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<ResultFilter>("all");
  const [selectedRow, setSelectedRow] = useState<number | null>(null);
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [sourceLoading, setSourceLoading] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editMeta, setEditMeta] = useState<Record<string, string>>(Object.create(null));
  const [editProducts, setEditProducts] = useState<OrderProduct[]>([]);
  const [editPortId, setEditPortId] = useState<number | null>(null);

  const load = useCallback(async () => {
    if (!Number.isFinite(orderId)) { setLoading(false); return; }
    try {
      const current = await getOrder(orderId);
      setOrder(current);
      if (current.group_id) {
        try { setWorkspace(await getArrangementWorkspace(current.group_id)); }
        catch { setWorkspace(null); }
      } else setWorkspace(null);
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "PO 加载失败");
    } finally { setLoading(false); }
  }, [orderId]);

  useEffect(() => { void load(); void getPortsList().then(setPorts); }, [load]);
  useEffect(() => {
    if (!order || !["uploading", "extracting", "matching"].includes(order.status)) return;
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [order, load]);

  const matchRows = useMemo(() => order?.match_results || [], [order]);
  const visibleRows = useMemo(() => matchRows.map((result, index) => ({ result, index })).filter(({ result }) => {
    if (filter !== "all" && result.match_status !== filter) return false;
    const term = search.trim().toLocaleLowerCase();
    return !term || [result.product_name, result.product_code, result.matched_product?.product_name_en, result.matched_product?.code].some((value) => value?.toLocaleLowerCase().includes(term));
  }), [matchRows, filter, search]);

  useEffect(() => {
    if (!order || selectedRow != null) return;
    const firstProblem = matchRows.findIndex((result, index) => result.match_status !== "matched" || !!rowFinding(order, result, index));
    setSelectedRow(firstProblem >= 0 ? firstProblem : matchRows.length ? 0 : null);
  }, [order, matchRows, selectedRow]);

  const runAction = async (work: () => Promise<unknown>, message: string) => {
    setBusy(true);
    try { await work(); await load(); toast.success(message); }
    catch (cause) { toast.error(cause instanceof Error ? cause.message : "操作失败"); }
    finally { setBusy(false); }
  };

  const openSource = async () => {
    if (!order) return;
    setActiveTab("source");
    if (sourceUrl || sourceLoading) return;
    setSourceLoading(true);
    try { setSourceUrl((await getOrderFilePreview(order.id)).url); }
    catch (cause) { toast.error(cause instanceof Error ? cause.message : "无法加载原始文件"); }
    finally { setSourceLoading(false); }
  };

  const openEdit = () => {
    if (!order) return;
    const meta = order.order_metadata || {};
    setEditMeta({
      po_number: String(meta.po_number || ""), ship_name: String(meta.ship_name || ""), vendor_name: String(meta.vendor_name || ""),
      loading_date: String(order.loading_date || meta.loading_date || ""), delivery_date: String(order.delivery_date || meta.delivery_date || ""),
      order_date: String(meta.order_date || ""), currency: String(meta.currency || ""),
    });
    setEditProducts(structuredClone(order.products || []));
    setEditPortId(order.port_id);
    setEditOpen(true);
  };

  const saveEdit = async () => {
    if (!order) return;
    if (editProducts.some((item) => !item.product_name.trim())) { toast.error("商品名称不能为空"); return; }
    setBusy(true);
    try {
      const normalizedMeta = Object.fromEntries(
        Object.entries(editMeta).map(([key, value]) => [key, value.trim() || null]),
      );
      await updateOrder(order.id, {
        order_metadata: { ...(order.order_metadata || {}), ...normalizedMeta },
        products: editProducts,
        po_number: editMeta.po_number.trim() || null,
        ship_name: editMeta.ship_name.trim() || null,
        vendor_name: editMeta.vendor_name.trim() || null,
        order_date: editMeta.order_date.trim() || null,
        currency: editMeta.currency.trim() || null,
        port_id: editPortId,
        loading_date: editMeta.loading_date.trim() || null,
        delivery_date: editMeta.delivery_date.trim() || null,
      });
      await rematchOrder(order.id);
      setEditOpen(false);
      await load();
      toast.success("PO 数据已保存并重新匹配");
    } catch (cause) { toast.error(cause instanceof Error ? cause.message : "保存失败"); }
    finally { setBusy(false); }
  };

  if (loading) return <div className="p-6 text-sm text-muted-foreground">正在加载 PO…</div>;
  if (!order) return <div className="flex h-full items-center justify-center"><div className="text-center"><p className="text-sm text-destructive">PO 不存在或没有查看权限</p><Button variant="link" onClick={() => router.push("/dashboard/orders")}>返回订单管理</Button></div></div>;

  const metadata = order.order_metadata || {};
  const portName = workspace?.arrangement.port || ports.find((item) => item.id === order.port_id)?.name || "未选择";
  const parentHref = order.group_id ? `/dashboard/orders/arrangements/${order.group_id}` : "/dashboard/orders";
  const parentName = workspace?.arrangement.ship || "未分类 PO";
  const selectedResult = selectedRow != null ? matchRows[selectedRow] : undefined;
  const selectedFinding = selectedResult && selectedRow != null ? rowFinding(order, selectedResult, selectedRow) : undefined;
  const status = orderDetailStatus(order);
  const matched = order.match_statistics?.matched ?? matchRows.filter((item) => item.match_status === "matched").length;
  const unmatched = order.match_statistics?.not_matched ?? matchRows.length - matched;
  const findings = order.anomaly_data?.findings || [];

  return (
    <div className="h-full overflow-y-auto bg-slate-50/40 p-5 text-sm dark:bg-transparent">
      <div className="mx-auto max-w-[1600px] space-y-3">
        <nav aria-label="面包屑" className="flex items-center gap-2 text-xs text-muted-foreground"><Link href="/dashboard/orders">订单管理</Link>{order.group_id ? <><span>›</span><Link href={parentHref}>{parentName}</Link></> : null}<span>›</span><span className="text-foreground">{String(metadata.po_number || `PO #${order.id}`)}</span></nav>
        <header className="flex min-h-11 flex-wrap items-center gap-3"><h1 className="text-xl font-semibold tracking-tight">{String(metadata.po_number || `PO #${order.id}`)}</h1><span className="text-xs text-muted-foreground">处理状态：</span><StatusText {...status} /><div className="ml-auto flex items-center gap-2"><Button asChild variant="outline" size="sm"><Link href={parentHref}>{order.group_id ? "返回整单" : "返回订单管理"}</Link></Button><Button size="sm" disabled={busy} onClick={openEdit}>编辑 PO</Button><Button variant="outline" size="sm" onClick={() => void openSource()}><FileText />查看原始文件</Button><DropdownMenu><DropdownMenuTrigger asChild><Button variant="outline" size="sm" disabled={busy}>更多<ChevronDown /></Button></DropdownMenuTrigger><DropdownMenuContent align="end"><DropdownMenuItem onSelect={() => void runAction(() => rematchOrder(order.id), "已重新匹配")}>重新匹配</DropdownMenuItem><DropdownMenuItem onSelect={() => void runAction(() => runAnomalyCheck(order.id), "异常检测已完成")}>重新运行异常检测</DropdownMenuItem><DropdownMenuItem onSelect={() => void runAction(() => reprocessOrder(order.id), "已开始重新处理文件")}>重新处理文件</DropdownMenuItem>{!order.is_reviewed ? <DropdownMenuItem onSelect={() => void runAction(() => reviewOrder(order.id), "已标记为审核完成")}>标记已审核</DropdownMenuItem> : null}</DropdownMenuContent></DropdownMenu></div></header>

        {order.processing_error ? <div className="rounded-[3px] border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950/20">处理失败：{order.processing_error}</div> : null}

        <Panel title="PO 基本信息"><div className="grid grid-cols-2 md:grid-cols-5">{[
          ["所属供船订单", parentName], ["装船日期", order.loading_date || String(metadata.loading_date || "待确认")], ["目标港口", portName], ["接收文件", order.filename], ["接收时间", formatBusinessDateTime(order.created_at)],
          ["商品数量", `${order.product_count || order.products?.length || 0} 项`], ["匹配成功", `${matched} 项`], ["未匹配", `${unmatched} 项`], ["数据检查", `${Math.max((order.product_count || 0) - (order.actionable_count ?? order.anomaly_data?.total_anomalies ?? 0), 0)}/${order.product_count || 0} 通过`], ["最后处理", formatBusinessDateTime(order.processed_at || order.updated_at)],
        ].map(([label, value]) => <div key={label} className="min-h-[70px] border-b border-r px-4 py-2.5 md:[&:nth-child(5n)]:border-r-0 md:[&:nth-last-child(-n+5)]:border-b-0"><div className="text-xs text-muted-foreground">{label}</div><div className="mt-1.5 truncate text-sm font-medium" title={value}>{value}</div></div>)}</div></Panel>

        <div className="flex border-b text-sm">{([
          ["products", "商品与匹配"], ["info", "PO 信息"], ["source", "原始文件"], ["history", "处理记录"],
        ] as Array<[Tab, string]>).map(([key, label]) => <button key={key} type="button" className={`border-b-2 px-5 py-2.5 ${activeTab === key ? "border-primary font-semibold text-primary" : "border-transparent text-muted-foreground hover:text-foreground"}`} onClick={() => key === "source" ? void openSource() : setActiveTab(key)}>{label}</button>)}</div>

        {activeTab === "products" ? <div className="grid items-start gap-3 xl:grid-cols-[minmax(0,1fr)_360px]">
          <Panel title={`商品明细（${matchRows.length || order.products?.length || 0}）`} aside={<div className="flex items-center gap-2"><select aria-label="筛选匹配结果" className="h-7 rounded-[3px] border bg-background px-2 text-xs" value={filter} onChange={(event) => setFilter(event.target.value as ResultFilter)}><option value="all">全部结果</option><option value="matched">匹配成功</option><option value="not_matched">未匹配</option></select><Input aria-label="搜索商品" className="h-7 w-44 text-xs" placeholder="搜索商品" value={search} onChange={(event) => setSearch(event.target.value)} /></div>}>
            <div className="overflow-x-auto"><table className="w-full min-w-[860px] border-collapse text-xs"><thead className="bg-slate-50 text-left text-muted-foreground dark:bg-slate-900/40"><tr>{["行", "PO 商品名称", "数量", "单位", "数据库匹配", "供应商", "检查结果", "操作"].map((item) => <th key={item} className="border-b border-r px-3 py-2 font-medium last:border-r-0">{item}</th>)}</tr></thead><tbody>
              {visibleRows.length ? visibleRows.map(({ result, index }) => { const finding = rowFinding(order, result, index); const problem = result.match_status !== "matched" || !!finding; return <tr key={`${result.product_code || "row"}-${index}`} className={`cursor-pointer border-b last:border-b-0 ${selectedRow === index ? "bg-amber-50/70 dark:bg-amber-950/15" : "hover:bg-slate-50/70 dark:hover:bg-slate-900/30"}`} onClick={() => setSelectedRow(index)}><td className="border-r px-3 py-2">{index + 1}</td><td className="max-w-[260px] border-r px-3 py-2 font-medium">{result.product_name || "—"}</td><td className="border-r px-3 py-2">{result.quantity ?? "—"}</td><td className="border-r px-3 py-2">{result.unit || "—"}</td><td className="border-r px-3 py-2"><StatusText label={result.match_status === "matched" ? "匹配成功" : "未匹配"} tone={result.match_status === "matched" ? "success" : "warning"} /></td><td className="border-r px-3 py-2">{supplierName(result, workspace)}</td><td className="border-r px-3 py-2"><StatusText label={problem ? (finding?.severity === "warning" ? "需要关注" : "需要确认") : "通过"} tone={problem ? "warning" : "success"} /></td><td className="px-3 py-2 text-primary">{problem ? "处理" : "查看"}</td></tr>; }) : <tr><td colSpan={8} className="px-3 py-10 text-center text-muted-foreground">暂无匹配结果；可从“更多”重新运行匹配。</td></tr>}
            </tbody></table></div>
          </Panel>
          <div className="space-y-3">
            <Panel title={`待处理商品（${order.actionable_count ?? (findings.length || unmatched)}）`}>
              {selectedResult ? <div className="text-xs"><dl className="divide-y">{[
                ["异常类型", selectedResult.match_status === "matched" ? selectedFinding?.code || "数据检查" : "商品未匹配"], ["发生位置", `第 ${(selectedRow || 0) + 1} 行`], ["原始名称", selectedResult.product_name || "—"], ["原因", selectedFinding?.message || selectedResult.match_reason || "检查通过"], ["处理状态", selectedResult.match_status === "matched" && !selectedFinding ? "无需处理" : "等待人工确认"],
              ].map(([label, value]) => <div key={label} className="grid grid-cols-[88px_1fr]"><dt className="border-r bg-slate-50 px-3 py-2 text-muted-foreground dark:bg-slate-900/40">{label}</dt><dd className="px-3 py-2 leading-5">{value}</dd></div>)}</dl><div className="grid grid-cols-2 gap-2 border-t p-3"><Button size="sm" disabled={busy} onClick={() => void runAction(() => rematchOrder(order.id), "已重新匹配")}>重新匹配</Button><Button variant="outline" size="sm" onClick={openEdit}>编辑 PO 数据</Button></div></div> : <div className="px-3 py-6 text-xs text-muted-foreground">选择一行查看数据与异常详情。</div>}
            </Panel>
            <Panel title="自动处理状态"><div className="text-xs">{(order.anomaly_data?.pipeline?.length ? order.anomaly_data.pipeline : [
              { step: 1, name: "文件提取", status: ["ready", "extracted"].includes(order.status) ? "completed" : order.status },
              { step: 2, name: "字段校验", status: order.anomaly_data ? "completed" : "pending" },
              { step: 3, name: "商品匹配", status: unmatched ? "needs_review" : matched ? "completed" : "pending" },
              { step: 4, name: "异常检测", status: findings.length ? "needs_review" : order.anomaly_data ? "completed" : "pending" },
            ]).slice(0, 8).map((stage) => <div key={`${stage.step}-${stage.name}`} className="grid grid-cols-[1fr_110px] border-b last:border-b-0"><span className="border-r px-3 py-2">{stage.name}</span><span className="px-3 py-2"><StatusText label={stage.status === "completed" ? "完成" : stage.status === "needs_review" || stage.status.includes("anomal") ? "需要处理" : stage.status === "failed" ? "失败" : "等待"} tone={stage.status === "completed" ? "success" : stage.status === "failed" ? "danger" : stage.status === "needs_review" || stage.status.includes("anomal") ? "warning" : "neutral"} /></span></div>)}</div></Panel>
          </div>
        </div> : null}

        {activeTab === "info" ? <Panel title="PO 信息" aside={<Button variant="outline" size="sm" disabled={busy} onClick={openEdit}>编辑 PO 信息</Button>}><div className="grid grid-cols-2 md:grid-cols-4">{[
          ["PO 编号", String(metadata.po_number || "—")], ["邮轮", String(metadata.ship_name || "—")], ["客户/供应商", String(metadata.vendor_name || "—")], ["订单日期", String(metadata.order_date || "—")], ["装船日期", order.loading_date || "—"], ["交付日期", order.delivery_date || "—"], ["目标港口", portName], ["币种", String(metadata.currency || "—")], ["总金额", order.total_amount != null ? String(order.total_amount) : "—"], ["提取模板", order.template_id ? `#${order.template_id} · ${order.template_match_method || "自动"}` : "—"], ["审核状态", order.is_reviewed ? "已审核" : "未审核"], ["源文档", order.document_id ? `#${order.document_id}` : "—"],
        ].map(([label, value]) => <div key={label} className="min-h-16 border-b border-r px-4 py-2.5 md:[&:nth-child(4n)]:border-r-0"><div className="text-xs text-muted-foreground">{label}</div><div className="mt-1.5 text-sm font-medium">{value}</div></div>)}</div></Panel> : null}

        {activeTab === "source" ? <Panel title="原始文件" aside={<Button variant="outline" size="sm" onClick={() => void downloadOrderFile(order.id, order.filename)}><Download />下载文件</Button>}><div className="h-[640px] bg-slate-100 dark:bg-slate-950">{sourceLoading ? <div className="flex h-full items-center justify-center"><Loader2 className="animate-spin" /></div> : sourceUrl && order.file_type === "pdf" ? <iframe src={sourceUrl} title="原始 PO 文件" className="h-full w-full border-0" /> : <div className="flex h-full flex-col items-center justify-center gap-3 text-muted-foreground"><FileText className="h-10 w-10" /><span className="text-sm">此文件类型请下载后查看</span></div>}</div></Panel> : null}

        {activeTab === "history" ? <div className="grid gap-3 xl:grid-cols-2"><Panel title="自动处理记录"><table className="w-full border-collapse text-xs"><thead className="bg-slate-50 text-left text-muted-foreground dark:bg-slate-900/40"><tr><th className="w-20 border-b border-r px-3 py-2 font-medium">步骤</th><th className="border-b border-r px-3 py-2 font-medium">环节</th><th className="w-28 border-b border-r px-3 py-2 font-medium">状态</th><th className="border-b px-3 py-2 font-medium">说明</th></tr></thead><tbody>{order.anomaly_data?.pipeline?.length ? order.anomaly_data.pipeline.map((stage) => <tr key={`${stage.step}-${stage.name}`} className="border-b last:border-b-0"><td className="border-r px-3 py-2">{stage.step}</td><td className="border-r px-3 py-2">{stage.name}</td><td className="border-r px-3 py-2">{stage.status}</td><td className="px-3 py-2 text-muted-foreground">{stage.message || "—"}</td></tr>) : <tr><td colSpan={4} className="px-3 py-8 text-center text-muted-foreground">暂无结构化处理记录</td></tr>}</tbody></table></Panel><Panel title={`异常记录（${findings.length}）`}><div className="divide-y text-xs">{findings.length ? findings.map((finding, index) => <div key={`${finding.code}-${index}`} className="grid grid-cols-[90px_1fr] gap-3 px-3 py-3"><StatusText label={finding.severity === "warning" ? "提醒" : finding.severity === "blocking" ? "阻断" : "错误"} tone={finding.severity === "warning" ? "warning" : "danger"} /><div><div className="font-medium">{finding.product_name || finding.product_code || "订单级检查"}</div><div className="mt-1 text-muted-foreground">{finding.message}</div>{finding.suggestion ? <div className="mt-1">建议：{finding.suggestion}</div> : null}</div></div>) : <div className="px-3 py-8 text-center text-muted-foreground">未发现异常</div>}</div></Panel></div> : null}

        <div className="rounded-[3px] border bg-blue-50/60 px-3 py-2 text-xs text-blue-900 dark:bg-blue-950/20 dark:text-blue-200">单张 PO 页面只处理数据、匹配和异常；供应商询价单由供船订单统一生成。</div>
      </div>

      <Dialog open={editOpen} onOpenChange={(open) => { if (!busy) setEditOpen(open); }}><DialogContent className="max-h-[88vh] max-w-5xl overflow-y-auto"><DialogHeader><DialogTitle>编辑 PO 数据</DialogTitle><DialogDescription>保存后系统会重新匹配商品并更新异常检测结果。</DialogDescription></DialogHeader><div className="grid grid-cols-2 gap-3 md:grid-cols-4">{[
        ["po_number", "PO 编号"], ["ship_name", "邮轮"], ["vendor_name", "客户/供应商"], ["order_date", "订单日期"], ["loading_date", "装船日期"], ["delivery_date", "交付日期"], ["currency", "币种"],
      ].map(([key, label]) => <label key={key} className="space-y-1 text-xs"><span className="text-muted-foreground">{label}</span><Input className="h-8 text-xs" value={editMeta[key] || ""} onChange={(event) => setEditMeta((current) => ({ ...current, [key]: event.target.value }))} /></label>)}<label className="space-y-1 text-xs"><span className="text-muted-foreground">目标港口</span><select className="h-8 w-full rounded-[3px] border bg-background px-2" value={editPortId ?? ""} onChange={(event) => setEditPortId(event.target.value ? Number(event.target.value) : null)}><option value="">未选择</option>{ports.map((port) => <option key={port.id} value={port.id}>{port.name}{port.country_name ? ` · ${port.country_name}` : ""}</option>)}</select></label></div><Panel title={`商品（${editProducts.length}）`} aside={<Button variant="outline" size="sm" onClick={() => setEditProducts((items) => [...items, { product_name: "", quantity: null, unit: "", unit_price: null }])}><Plus />添加商品</Button>}><div className="overflow-x-auto"><table className="w-full min-w-[760px] text-xs"><thead className="bg-slate-50 text-left text-muted-foreground"><tr>{["商品名称", "商品代码", "数量", "单位", "单价", ""].map((item) => <th key={item} className="border-b px-2 py-2 font-medium">{item}</th>)}</tr></thead><tbody>{editProducts.map((product, index) => <tr key={index} className="border-b"><td className="p-1.5"><Input className="h-8 text-xs" value={product.product_name} onChange={(event) => setEditProducts((items) => items.map((item, row) => row === index ? { ...item, product_name: event.target.value } : item))} /></td><td className="p-1.5"><Input className="h-8 text-xs" value={product.product_code || ""} onChange={(event) => setEditProducts((items) => items.map((item, row) => row === index ? { ...item, product_code: event.target.value } : item))} /></td><td className="p-1.5"><Input className="h-8 text-xs" type="number" value={product.quantity ?? ""} onChange={(event) => setEditProducts((items) => items.map((item, row) => row === index ? { ...item, quantity: event.target.value === "" ? null : Number(event.target.value) } : item))} /></td><td className="p-1.5"><Input className="h-8 text-xs" value={product.unit || ""} onChange={(event) => setEditProducts((items) => items.map((item, row) => row === index ? { ...item, unit: event.target.value } : item))} /></td><td className="p-1.5"><Input className="h-8 text-xs" type="number" value={product.unit_price ?? ""} onChange={(event) => setEditProducts((items) => items.map((item, row) => row === index ? { ...item, unit_price: event.target.value === "" ? null : Number(event.target.value) } : item))} /></td><td className="p-1.5"><Button variant="ghost" size="icon-sm" aria-label="删除商品" onClick={() => setEditProducts((items) => items.filter((_, row) => row !== index))}><Trash2 /></Button></td></tr>)}</tbody></table></div></Panel><div className="flex justify-end gap-2"><Button variant="outline" disabled={busy} onClick={() => setEditOpen(false)}>取消</Button><Button disabled={busy} onClick={() => void saveEdit()}>{busy ? <Loader2 className="animate-spin" /> : null}保存并重新匹配</Button></div></DialogContent></Dialog>
    </div>
  );
}
