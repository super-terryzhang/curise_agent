"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ChevronDown, Download, MoreHorizontal, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { deleteOrder, downloadInquiryZip, getPortsList, type PortItem } from "@/lib/orders-api";
import {
  classifyOrder,
  deleteOrderGroup,
  generateInquiryForGroup,
  getArrangementWorkspace,
  listGroupInquiries,
  removeOrderFromGroup,
  updateArrangementWorkspace,
  updateOrderGroup,
  type ArrangementInquiryVersion,
  type ArrangementOrder,
  type ArrangementWorkspace,
  type ArrangementWorkspaceSupplier,
} from "@/lib/order-groups-api";
import {
  arrangementPoStatus,
  arrangementWorkspaceStatus,
  formatBusinessDateTime,
  supplierInquiryStatus,
  type BusinessTone,
} from "@/lib/order-workspace-view";

type Edit = { kind: "rename" | "dissolve" | "delete"; order?: ArrangementOrder };

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
  return (
    <section className={`overflow-hidden rounded-[3px] border bg-background ${className}`}>
      <div className="flex min-h-9 items-center gap-3 border-b bg-slate-50 px-3 py-1.5 dark:bg-slate-900/50">
        <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">{title}</h2>
        {aside ? <div className="ml-auto text-xs text-muted-foreground">{aside}</div> : null}
      </div>
      {children}
    </section>
  );
}

function supplierRowsForVersion(workspace: ArrangementWorkspace, version: ArrangementInquiryVersion | undefined): ArrangementWorkspaceSupplier[] {
  if (!version || version.id === workspace.latest_inquiry?.id) return workspace.suppliers;
  return version.suppliers.map((supplier) => ({
    supplier_id: supplier.supplier_id,
    supplier_name: supplier.supplier_name || `供应商 #${supplier.supplier_id}`,
    product_count: supplier.product_count,
    source_order_count: 0,
    source_po_numbers: [],
    status: supplier.status,
    error_message: supplier.error_message,
    template_id: supplier.template?.id ?? null,
    template_name: supplier.template?.name ?? null,
    template_method: supplier.template?.method || "unavailable",
  }));
}

export default function ArrangementDetailPage() {
  const params = useParams();
  const router = useRouter();
  const groupId = Number(params.id);
  const [workspace, setWorkspace] = useState<ArrangementWorkspace | null>(null);
  const [versions, setVersions] = useState<ArrangementInquiryVersion[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [selectedSupplier, setSelectedSupplier] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [edit, setEdit] = useState<Edit | null>(null);
  const [name, setName] = useState("");
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [detailShip, setDetailShip] = useState("");
  const [detailDate, setDetailDate] = useState("");
  const [detailPortId, setDetailPortId] = useState<number | null>(null);
  const [ports, setPorts] = useState<PortItem[]>([]);

  const load = useCallback(async () => {
    if (!Number.isFinite(groupId)) { setError("供船订单编号无效"); setLoading(false); return; }
    setLoading(true);
    try {
      const [current, history] = await Promise.all([getArrangementWorkspace(groupId), listGroupInquiries(groupId)]);
      setWorkspace(current);
      setVersions(history);
      setSelectedVersion((previous) => history.some((item) => item.version === previous) ? previous : history[0]?.version ?? null);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "供船订单加载失败");
    } finally { setLoading(false); }
  }, [groupId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void getPortsList().then(setPorts).catch(() => setPorts([])); }, []);
  useEffect(() => {
    const processing = workspace?.arrangement.orders.some((order) => ["uploading", "extracting", "matching"].includes(order.status))
      || ["pending", "in_progress"].includes(workspace?.latest_inquiry?.status || "");
    if (!processing) return;
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [workspace, load]);

  const action = async (work: () => Promise<void>) => {
    setBusy(true);
    try { await work(); await load(); }
    catch (cause) { toast.error(cause instanceof Error ? cause.message : "操作失败"); }
    finally { setBusy(false); }
  };
  const currentVersion = versions.find((item) => item.version === selectedVersion);
  const suppliers = useMemo(() => workspace ? supplierRowsForVersion(workspace, currentVersion) : [], [workspace, currentVersion]);
  const isLatestVersion = !currentVersion || currentVersion.id === workspace?.latest_inquiry?.id;
  const inquiryRunning = ["pending", "in_progress"].includes(workspace?.latest_inquiry?.status || "");
  const startInquiry = () => action(async () => {
    const result = await generateInquiryForGroup(groupId);
    setSelectedVersion(result.version);
    toast.success(`已开始生成询价版本 ${result.version}`);
  });

  const saveEdit = () => action(async () => {
    if (!edit || !workspace) return;
    if (edit.kind === "rename") {
      await updateOrderGroup(groupId, { name: name.trim() });
      toast.success("供船订单名称已更新");
    } else if (edit.kind === "dissolve") {
      await deleteOrderGroup(groupId);
      toast.success("供船订单已解散，成员 PO 已移至未分类");
      router.push("/dashboard/orders");
    } else if (edit.order) {
      await deleteOrder(edit.order.id);
      toast.success("PO 已删除");
    }
    setEdit(null);
  });
  const openDetails = () => {
    if (!workspace) return;
    setDetailShip(workspace.arrangement.ship || "");
    setDetailDate(workspace.arrangement.day || "");
    setDetailPortId(workspace.arrangement.port_id);
    setDetailsOpen(true);
  };
  const saveDetails = () => action(async () => {
    if (!detailShip.trim() || !detailDate || detailPortId == null) {
      throw new Error("请完整填写邮轮、装船日期和目标港口");
    }
    await updateArrangementWorkspace(groupId, {
      ship_name: detailShip.trim(),
      loading_date: detailDate,
      port_id: detailPortId,
    });
    setDetailsOpen(false);
    toast.success("供船订单信息已更新，商品已按新港口和日期重新匹配");
  });

  if (loading && !workspace) return <div className="p-6 text-sm text-muted-foreground">正在加载供船订单…</div>;
  if (!workspace) return <div className="space-y-4 p-6"><Button asChild size="sm" variant="outline"><Link href="/dashboard/orders">返回订单管理</Link></Button><div role="alert" className="rounded-[3px] border border-destructive/30 p-4 text-sm text-destructive">{error || "供船订单不存在"}</div></div>;

  const group = workspace.arrangement;
  const status = arrangementWorkspaceStatus(workspace);
  const generatedCount = suppliers.filter((item) => item.status === "completed").length;
  const missingTemplateCount = suppliers.filter((item) => !item.template_id).length;
  const completePoCount = group.orders.filter((item) => arrangementPoStatus(item).tone === "success").length;

  return (
    <div className="h-full overflow-y-auto bg-slate-50/40 p-5 text-sm dark:bg-transparent">
      <div className="mx-auto max-w-[1600px] space-y-3">
        <nav aria-label="面包屑" className="flex items-center gap-2 text-xs text-muted-foreground"><Link className="hover:text-foreground" href="/dashboard/orders">订单管理</Link><span>›</span><span>供船订单</span><span>›</span><span className="text-foreground">{group.ship}</span></nav>
        <header className="flex min-h-11 flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold tracking-tight">{group.ship}</h1><span className="text-xs text-muted-foreground">处理状态：</span><StatusText {...status} />
          <div className="ml-auto flex items-center gap-2">
            <Button variant="outline" size="sm" disabled={loading || busy} onClick={() => void load()}><RefreshCw className={loading ? "animate-spin" : ""} />刷新</Button>
            {group.can_manage ? <Button size="sm" disabled={busy} onClick={openDetails}>编辑订单信息</Button> : null}
            {group.can_manage ? <DropdownMenu><DropdownMenuTrigger asChild><Button variant="outline" size="sm">整单设置<ChevronDown /></Button></DropdownMenuTrigger><DropdownMenuContent align="end"><DropdownMenuItem onSelect={() => { setName(group.name); setEdit({ kind: "rename" }); }}>重命名</DropdownMenuItem><DropdownMenuItem variant="destructive" onSelect={() => setEdit({ kind: "dissolve" })}>解散整单</DropdownMenuItem></DropdownMenuContent></DropdownMenu> : null}
            {group.can_generate_inquiry ? <Button size="sm" disabled={busy || inquiryRunning || group.orders.length === 0} onClick={() => void startInquiry()}>{inquiryRunning ? "询价生成中…" : "生成询价单"}</Button> : null}
          </div>
        </header>
        {error ? <div role="alert" className="rounded-[3px] border border-destructive/30 bg-background px-3 py-2 text-xs text-destructive">{error}</div> : null}
        {workspace.latest_inquiry?.inputs_changed ? <div role="status" className="rounded-[3px] border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200">订单信息或商品匹配已修改，现有询价版本仍作为历史保留；请确认后重新生成一个新版本。</div> : null}

        <Panel title="供船订单信息">
          <div className="grid grid-cols-2 md:grid-cols-5">
            {[
              ["装船日期", group.day || "待确认"], ["目标港口", group.port], ["PO 数量", `${group.orders.length} 张`], ["商品数量", `${workspace.summary.product_count} 项`], ["供应商", `${workspace.summary.supplier_count} 家`],
              ["异常", workspace.summary.anomaly_count ? `${workspace.summary.anomaly_count} 项待处理` : "无"], ["PO 检查", `${completePoCount}/${group.orders.length} 完成`], ["商品匹配", `${workspace.summary.matched_count}/${workspace.summary.product_count}`], ["询价单", workspace.latest_inquiry ? `版本 ${workspace.latest_inquiry.version} · ${workspace.latest_inquiry.status}` : "未生成"], ["最后更新", formatBusinessDateTime(workspace.summary.updated_at)],
            ].map(([label, value]) => <div key={label} className="min-h-[70px] border-b border-r px-4 py-2.5 last:border-r-0 md:[&:nth-child(5n)]:border-r-0 md:[&:nth-last-child(-n+5)]:border-b-0"><div className="text-xs text-muted-foreground">{label}</div><div className="mt-1.5 text-sm font-medium">{value}</div></div>)}
          </div>
        </Panel>

        <div className="grid items-start gap-3 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <div className="space-y-3">
            <Panel title={`PO 清单（${group.orders.length}）`}>
              <div className="overflow-x-auto"><table className="w-full min-w-[660px] border-collapse text-xs"><thead className="bg-slate-50 text-left text-muted-foreground dark:bg-slate-900/40"><tr>{["PO 编号", "接收时间", "商品", "处理状态", "异常", "操作"].map((item) => <th key={item} className="border-b border-r px-3 py-2 font-medium last:border-r-0">{item}</th>)}</tr></thead><tbody>
                {group.orders.map((order) => { const poStatus = arrangementPoStatus(order); return <tr key={order.id} className="border-b last:border-b-0 hover:bg-slate-50/70 dark:hover:bg-slate-900/30"><td className="border-r px-3 py-2 font-medium">{order.po_number || `PO #${order.id}`}</td><td className="border-r px-3 py-2 text-muted-foreground">{formatBusinessDateTime(order.created_at)}</td><td className="border-r px-3 py-2">{order.product_count} 项</td><td className="border-r px-3 py-2"><StatusText {...poStatus} /></td><td className={`border-r px-3 py-2 ${order.anomaly_count ? "font-medium text-red-600" : ""}`}>{order.anomaly_count || 0}</td><td className="px-2 py-1.5"><div className="flex items-center gap-1"><Button asChild variant="link" size="sm" className="h-7 px-1 text-xs"><Link href={`/dashboard/orders/${order.id}`}>详情</Link></Button>{group.can_manage ? <DropdownMenu><DropdownMenuTrigger asChild><Button variant="ghost" size="icon-sm" disabled={busy} aria-label="PO 操作"><MoreHorizontal /></Button></DropdownMenuTrigger><DropdownMenuContent align="end"><DropdownMenuItem onSelect={() => void action(async () => { await removeOrderFromGroup(group.id, order.id); toast.success("PO 已移至未分类"); })}>移至未分类</DropdownMenuItem><DropdownMenuItem onSelect={() => void action(async () => { const result = await classifyOrder(order.id); result.group_id ? toast.success("PO 已重新归类") : toast.info(result.reason || "请补充 PO 信息"); })}>重新自动归类</DropdownMenuItem><DropdownMenuItem variant="destructive" onSelect={() => setEdit({ kind: "delete", order })}>删除 PO</DropdownMenuItem></DropdownMenuContent></DropdownMenu> : null}</div></td></tr>; })}
              </tbody></table></div>
            </Panel>
            <Panel title="PO 处理提醒"><div className="divide-y text-xs">{workspace.unassigned_items.length ? workspace.unassigned_items.slice(0, 5).map((item, index) => <div key={`${item.source_order_id}-${item.source_line}-${index}`} className="flex items-center gap-3 px-3 py-2"><span className="h-2 w-2 rounded-full bg-amber-500" /><span className="font-medium">{item.source_po_number || `PO #${item.source_order_id}`}</span><span className="text-muted-foreground">第 {item.source_line || "?"} 行 · {item.product_name || "商品名称缺失"}</span><span className="ml-auto text-amber-700 dark:text-amber-300">{item.reason}</span></div>) : <div className="px-3 py-3 text-muted-foreground">当前没有需要人工处理的 PO 商品。</div>}</div></Panel>
          </div>

          <Panel title="供应商询价单（整单）" aside={<div className="flex items-center gap-3"><span>汇总全部 PO 后按供应商生成</span>{versions.length ? <select className="h-7 rounded-[3px] border bg-background px-2 text-xs text-foreground" value={selectedVersion ?? ""} onChange={(event) => setSelectedVersion(Number(event.target.value))}>{versions.map((item) => <option key={item.id} value={item.version}>版本 {item.version}</option>)}</select> : null}</div>}>
            {currentVersion?.status === "error" ? <div role="alert" className="border-b border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900 dark:bg-red-950/20 dark:text-red-300"><span className="font-semibold">版本 {currentVersion.version} 生成失败：</span>{currentVersion.error_message || "后台生成发生错误，请检查数据后重新生成。"}</div> : null}
            <div className="overflow-x-auto"><table className="w-full min-w-[840px] border-collapse text-xs"><thead className="bg-slate-50 text-left text-muted-foreground dark:bg-slate-900/40"><tr>{["供应商", "商品行", "来源 PO", "询价模板", "准备状态", "询价状态", "操作"].map((item) => <th key={item} className="border-b border-r px-3 py-2 font-medium last:border-r-0">{item}</th>)}</tr></thead><tbody>
              {suppliers.map((supplier) => { const inquiryStatus = supplierInquiryStatus(supplier); return <tr key={supplier.supplier_id} className="border-b last:border-b-0 hover:bg-slate-50/70 dark:hover:bg-slate-900/30"><td className="border-r px-3 py-2 font-medium">{supplier.supplier_name}</td><td className="border-r px-3 py-2">{supplier.product_count}</td><td className="border-r px-3 py-2">{isLatestVersion ? `${supplier.source_order_count} 张` : "历史快照"}</td><td className={`border-r px-3 py-2 ${supplier.template_name ? "" : "font-medium text-amber-700 dark:text-amber-300"}`}>{supplier.template_name || "未配置模板"}</td><td className="border-r px-3 py-2"><StatusText label={supplier.error_message || !supplier.template_id ? "需要处理" : "可以生成"} tone={supplier.error_message || !supplier.template_id ? "warning" : "success"} /></td><td className="border-r px-3 py-2"><StatusText {...inquiryStatus} /></td><td className="px-2 py-1.5"><Button variant="link" size="sm" className="h-7 px-1 text-xs" onClick={() => setSelectedSupplier((current) => current === supplier.supplier_id ? null : supplier.supplier_id)}>详情</Button></td></tr>; })}
              {isLatestVersion && workspace.unassigned_items.length ? <tr className="border-b bg-amber-50/50 dark:bg-amber-950/10"><td className="border-r px-3 py-2 font-medium">未分配商品</td><td className="border-r px-3 py-2">{workspace.unassigned_items.length}</td><td className="border-r px-3 py-2">{new Set(workspace.unassigned_items.map((item) => item.source_order_id)).size} 张</td><td className="border-r px-3 py-2">—</td><td className="border-r px-3 py-2"><StatusText label="需要处理" tone="warning" /></td><td className="border-r px-3 py-2"><StatusText label="不生成" tone="neutral" /></td><td className="px-3 py-2"><span className="text-amber-700 dark:text-amber-300">查看左侧提醒</span></td></tr> : null}
              {!suppliers.length && !workspace.unassigned_items.length ? <tr><td colSpan={7} className="px-3 py-8 text-center text-muted-foreground">尚无供应商询价数据</td></tr> : null}
            </tbody></table></div>
            {selectedSupplier != null ? (() => { const supplier = suppliers.find((item) => item.supplier_id === selectedSupplier); if (!supplier) return null; return <div className="border-t bg-slate-50/60 px-3 py-2 text-xs dark:bg-slate-900/30"><span className="font-medium">{supplier.supplier_name}</span><span className="ml-4 text-muted-foreground">来源 PO：{supplier.source_po_numbers.length ? supplier.source_po_numbers.join("、") : "历史版本未保存逐供应商来源"}</span>{supplier.error_message ? <span className="ml-4 text-red-600">{supplier.error_message}</span> : null}</div>; })() : null}
            <div className="flex flex-wrap items-center gap-4 border-t px-3 py-3 text-xs"><span>生成对象 <strong>{suppliers.length} 家</strong></span><span>商品行 <strong>{suppliers.reduce((sum, item) => sum + item.product_count, 0)}</strong></span><span>模板未配置 <strong className={missingTemplateCount ? "text-amber-700 dark:text-amber-300" : ""}>{missingTemplateCount} 家</strong></span><span>暂不生成 <strong>{isLatestVersion ? workspace.unassigned_items.length : currentVersion?.unassigned_count || 0} 行</strong></span><span>已生成 <strong>{generatedCount} 家</strong></span><div className="ml-auto flex gap-2">{isLatestVersion && currentVersion && ["completed", "partial"].includes(currentVersion.status) ? <Button variant="outline" size="sm" disabled={busy} onClick={() => void action(async () => { await downloadInquiryZip(currentVersion.order_id); })}><Download />下载询价文件</Button> : null}{group.can_generate_inquiry ? <Button size="sm" disabled={busy || inquiryRunning || !suppliers.length} onClick={() => void startInquiry()}>{inquiryRunning ? "询价生成中…" : `生成 ${suppliers.length} 份询价单`}</Button> : null}</div></div>
          </Panel>
        </div>

        <Panel title="处理记录"><div className="max-h-44 overflow-y-auto"><table className="w-full border-collapse text-xs"><thead className="bg-slate-50 text-left text-muted-foreground dark:bg-slate-900/40"><tr><th className="w-40 border-b border-r px-3 py-2 font-medium">时间</th><th className="border-b px-3 py-2 font-medium">内容</th></tr></thead><tbody>{workspace.activity.length ? workspace.activity.map((item, index) => <tr key={`${item.occurred_at}-${index}`} className="border-b last:border-b-0"><td className="border-r px-3 py-2 text-muted-foreground">{formatBusinessDateTime(item.occurred_at)}</td><td className="px-3 py-2">{item.message}</td></tr>) : <tr><td colSpan={2} className="px-3 py-4 text-center text-muted-foreground">暂无处理记录</td></tr>}</tbody></table></div></Panel>
      </div>

      <Dialog open={detailsOpen} onOpenChange={(open) => { if (!busy) setDetailsOpen(open); }}><DialogContent className="max-w-lg"><DialogHeader><DialogTitle>编辑供船订单信息</DialogTitle><DialogDescription>修改会同步到整单内全部 PO，并按新的装船日期和目标港口重新匹配商品；已有询价版本继续保留为历史。</DialogDescription></DialogHeader><div className="grid gap-3 sm:grid-cols-2"><label className="space-y-1 text-xs sm:col-span-2"><span className="text-muted-foreground">邮轮</span><Input value={detailShip} onChange={(event) => setDetailShip(event.target.value)} /></label><label className="space-y-1 text-xs"><span className="text-muted-foreground">装船日期</span><Input type="date" value={detailDate} onChange={(event) => setDetailDate(event.target.value)} /></label><label className="space-y-1 text-xs"><span className="text-muted-foreground">目标港口</span><select className="h-9 w-full rounded-[3px] border bg-background px-2" value={detailPortId ?? ""} onChange={(event) => setDetailPortId(event.target.value ? Number(event.target.value) : null)}><option value="">请选择</option>{ports.map((port) => <option key={port.id} value={port.id}>{port.name}{port.country_name ? ` · ${port.country_name}` : ""}</option>)}</select></label></div><div className="flex justify-end gap-2"><Button variant="outline" disabled={busy} onClick={() => setDetailsOpen(false)}>取消</Button><Button disabled={busy || !detailShip.trim() || !detailDate || detailPortId == null} onClick={() => void saveDetails()}>{busy ? "保存中…" : "保存并重新匹配"}</Button></div></DialogContent></Dialog>
      <Dialog open={!!edit} onOpenChange={(open) => { if (!open && !busy) setEdit(null); }}><DialogContent className="max-w-md"><DialogHeader><DialogTitle>{edit?.kind === "rename" ? "重命名供船订单" : edit?.kind === "dissolve" ? "解散供船订单" : "删除 PO"}</DialogTitle><DialogDescription>{edit?.kind === "rename" ? "重命名后，此整单由人工管理。" : edit?.kind === "dissolve" ? "整单中的 PO 将保留并移至未分类。" : `确认删除 ${edit?.order?.po_number || `PO #${edit?.order?.id}`}？`}</DialogDescription></DialogHeader>{edit?.kind === "rename" ? <Input aria-label="供船订单名称" maxLength={200} value={name} onChange={(event) => setName(event.target.value)} /> : null}<div className="flex justify-end gap-2"><Button variant="outline" disabled={busy} onClick={() => setEdit(null)}>取消</Button><Button variant={edit?.kind === "dissolve" || edit?.kind === "delete" ? "destructive" : "default"} disabled={busy || (edit?.kind === "rename" && !name.trim())} onClick={() => void saveEdit()}>{busy ? "处理中…" : "确认"}</Button></div></DialogContent></Dialog>
    </div>
  );
}
