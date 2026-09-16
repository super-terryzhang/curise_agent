"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChevronDown, ChevronRight, FileText, MoreHorizontal, RefreshCw, Search, Ship } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { deleteOrder } from "@/lib/orders-api";
import {
  assignOrdersToGroup,
  classifyOrder,
  createOrderGroup,
  listArrangements,
  removeOrderFromGroup,
  type ArrangementOrder,
  type ArrangementsResult,
} from "@/lib/order-groups-api";
import {
  arrangementPath,
  filterArrangements,
  matchesOrder,
  poProcessingLabel,
  type ArrangementFilters,
} from "@/lib/arrangements-view";

const INITIAL_FILTERS: ArrangementFilters = { search: "", from: "", to: "" };
const selectClass = "h-9 rounded-md border bg-background px-3 text-sm";
type Edit = { kind: "assign" | "delete"; order: ArrangementOrder; groupId?: number };

function PoRows({
  orders,
  unclassified = false,
  busy,
  onAssign,
  onRemove,
  onReclassify,
  onDelete,
}: {
  orders: ArrangementOrder[];
  unclassified?: boolean;
  busy: boolean;
  onAssign: (order: ArrangementOrder) => void;
  onRemove?: (order: ArrangementOrder) => void;
  onReclassify: (order: ArrangementOrder) => void;
  onDelete: (order: ArrangementOrder) => void;
}) {
  if (!orders.length) {
    return <p className="border-b bg-muted/10 px-8 py-6 text-sm text-muted-foreground">{unclassified ? "暂无未分类 PO" : "此供船订单暂无 PO"}</p>;
  }

  return (
    <div className="border-b bg-muted/10 px-5 py-3">
      <div className="ml-5 border-l-2 border-primary/70 pl-4">
        <div className="grid grid-cols-[minmax(150px,1.2fr)_100px_minmax(180px,1.5fr)_150px] rounded-t-md border bg-muted/30 px-4 py-2 text-xs text-muted-foreground">
          <span>PO 编号</span><span>商品数</span><span>处理状态</span><span>操作</span>
        </div>
        {orders.map(order => {
          const label = unclassified && order.reason ? order.reason : poProcessingLabel(order);
          const needsAttention = unclassified || order.requires_human_review || order.status === "error" || order.inquiry_status === "error";
          const completed = !needsAttention && label === "自动处理完成";
          return (
            <div key={order.id} data-testid="po-row" className="grid grid-cols-[minmax(150px,1.2fr)_100px_minmax(180px,1.5fr)_150px] items-center border-x border-b bg-background px-4 py-2.5 text-sm last:rounded-b-md">
              <Link className="truncate font-medium hover:underline" href={`/dashboard/orders/${order.id}`}>{order.po_number || `PO #${order.id}`}</Link>
              <span className="text-muted-foreground">{order.product_count} 项</span>
              <span className={`flex min-w-0 items-center gap-2 text-xs ${needsAttention ? "text-amber-700 dark:text-amber-300" : completed ? "text-emerald-700 dark:text-emerald-300" : "text-muted-foreground"}`}>
                <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${needsAttention ? "bg-amber-500" : completed ? "bg-emerald-600" : "bg-muted-foreground/50"}`} />
                <span className="truncate" title={label}>{label}</span>
              </span>
              <div className="flex items-center justify-end gap-1">
                <Button asChild size="sm" variant="ghost" className="text-primary"><Link href={`/dashboard/orders/${order.id}`}>查看 PO</Link></Button>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild><Button size="icon-sm" variant="ghost" disabled={busy} aria-label={`${order.po_number || `PO #${order.id}`} 更多操作`}><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onSelect={() => onAssign(order)}>手动指定供船订单</DropdownMenuItem>
                    {onRemove && <DropdownMenuItem onSelect={() => onRemove(order)}>移至未分类</DropdownMenuItem>}
                    <DropdownMenuItem onSelect={() => onReclassify(order)}>按 PO 信息重新自动归类</DropdownMenuItem>
                    <DropdownMenuItem variant="destructive" onSelect={() => onDelete(order)}>删除 PO</DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function OrdersPage() {
  const router = useRouter();
  const [data, setData] = useState<ArrangementsResult | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [filters, setFilters] = useState(INITIAL_FILTERS);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [edit, setEdit] = useState<Edit | null>(null);
  const [target, setTarget] = useState("");
  const [name, setName] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    try { setData(await listArrangements()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "加载失败"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    const onFocus = () => { void refresh(); };
    window.addEventListener("focus", onFocus);
    const processing = data && [...data.unclassified, ...data.arrangements.flatMap(group => group.orders)]
      .some(order => ["uploading", "extracting", "matching"].includes(order.status) || ["pending", "in_progress"].includes(order.inquiry_status || ""));
    const timer = processing ? window.setInterval(onFocus, 15000) : undefined;
    return () => { window.removeEventListener("focus", onFocus); if (timer) window.clearInterval(timer); };
  }, [data, refresh]);

  const today = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Tokyo" }).format(new Date());
  const groups = filterArrangements(data?.arrangements || [], filters, today);
  const unclassified = (data?.unclassified || []).filter(order => matchesOrder(order, filters));
  const filtered = Object.values(filters).some(Boolean);
  const patchFilter = (key: keyof ArrangementFilters, value: string) => setFilters(current => ({ ...current, [key]: value }));
  const toggle = (key: string) => setExpanded(current => {
    const next = new Set(current);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  const action = async (work: () => Promise<void>) => {
    setBusy(true);
    try { await work(); await refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "操作失败"); }
    finally { setBusy(false); }
  };
  const openEdit = (value: Edit) => { setEdit(value); setTarget(""); setName(""); };
  const reclassify = (order: ArrangementOrder) => action(async () => {
    const result = await classifyOrder(order.id);
    if (result.group_id) toast.success("PO 已自动归类"); else toast.info(result.reason || "请补充 PO 信息");
  });
  const save = () => action(async () => {
    if (!edit) return;
    if (edit.kind === "assign") {
      if (target === "new") await createOrderGroup({ name: name.trim(), order_ids: [edit.order.id], ship_name: edit.order.ship });
      else await assignOrdersToGroup(Number(target), [edit.order.id]);
      toast.success("已手动归类；后续自动扫描保留此选择");
    } else {
      await deleteOrder(edit.order.id);
      toast.success("PO 已删除");
    }
    setEdit(null);
  });

  return (
    <div className="flex h-full min-h-0 flex-col gap-5 p-6">
      <PageHeader title="订单管理" description="按装船日和目标港口管理供船订单" action={
        <Button size="sm" onClick={() => router.push("/dashboard/documents")}><FileText className="mr-1.5 h-4 w-4" />导入 PO</Button>
      } />
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-64 flex-1"><Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" /><Input aria-label="搜索供船订单" placeholder="搜索船名、PO 编号、港口" className="pl-9" value={filters.search} onChange={event => patchFilter("search", event.target.value)} /></div>
        <div className="flex items-center gap-1.5"><Input className="w-36" aria-label="开始日期" title="装船日开始日期" type="date" value={filters.from} onChange={event => patchFilter("from", event.target.value)} /><span className="text-muted-foreground">—</span><Input className="w-36" aria-label="结束日期" title="装船日结束日期" type="date" value={filters.to} onChange={event => patchFilter("to", event.target.value)} /></div>
        {filtered && <Button size="sm" variant="ghost" onClick={() => setFilters(INITIAL_FILTERS)}>清除筛选</Button>}
        <Button variant="ghost" size="icon" aria-label="刷新订单" disabled={loading || busy} onClick={() => void refresh()}><RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} /></Button>
      </div>
      {error && <div role="alert" className="rounded-md border border-destructive/30 p-3 text-sm text-destructive">{error}<Button variant="link" onClick={() => void refresh()}>重新加载</Button></div>}
      {!data && loading ? <p className="text-sm text-muted-foreground">正在加载供船订单…</p> : data && <>
        <div className="flex items-center justify-between text-xs text-muted-foreground"><span>{data.arrangements.length} 个供船订单 · {data.total_orders} 张 PO</span><span>近期安排优先 · 历史记录保留</span></div>
        <div data-testid="orders-page-scroll" className="min-h-0 flex-1 overflow-y-auto rounded-lg border">
          <div className="hidden grid-cols-[minmax(0,2fr)_140px_minmax(140px,1fr)_90px_180px] gap-4 border-b bg-muted/40 px-5 py-3 text-xs text-muted-foreground md:grid"><span>供船订单</span><span>装船日期</span><span>目标港口</span><span>PO 数</span><span>操作</span></div>
          <div data-testid="unclassified-row" className="flex items-center gap-4 border-b bg-background px-5 py-3.5">
            <div className={`rounded-md p-2 ${data.unclassified.length ? "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300" : "bg-muted text-muted-foreground"}`}><FileText className="h-4 w-4" /></div>
            <div className="min-w-0 flex-1"><div className="text-sm font-medium">未分类 PO <span className="ml-2 text-muted-foreground">{unclassified.length} 张</span></div><p className="mt-1 text-xs text-muted-foreground">{filtered ? `符合筛选 ${unclassified.length} 张，共 ${data.unclassified.length} 张` : data.unclassified.length ? "补充信息、核对冲突，或手动指定供船订单" : "所有 PO 均已归类"}</p></div>
            <Button size="icon-sm" variant="ghost" aria-label={expanded.has("unclassified") ? "收起未分类 PO" : "展开未分类 PO"} aria-expanded={expanded.has("unclassified")} onClick={() => toggle("unclassified")}>{expanded.has("unclassified") ? <ChevronDown /> : <ChevronRight />}</Button>
          </div>
          {expanded.has("unclassified") && <PoRows orders={unclassified} unclassified busy={busy} onAssign={order => openEdit({ kind: "assign", order })} onReclassify={order => void reclassify(order)} onDelete={order => openEdit({ kind: "delete", order })} />}

          {groups.map(group => {
            const key = String(group.id);
            const isOpen = expanded.has(key);
            return <div key={group.id}>
              <div data-testid="arrangement-row" className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2 border-b px-5 py-3.5 md:grid-cols-[minmax(0,2fr)_140px_minmax(140px,1fr)_90px_180px]">
                <div className="min-w-0"><div className="flex items-center gap-2 text-sm font-medium"><Ship className="h-4 w-4 shrink-0 text-muted-foreground" /><span className="truncate">{group.ship}</span>{group.manual && <span className="shrink-0 text-xs font-normal text-muted-foreground">人工</span>}</div></div>
                <div className="col-start-1 text-sm md:col-auto">{group.day || "日期待确认"}<span className="ml-1 text-xs text-muted-foreground md:block md:ml-0">{group.date_basis === "loading_date" ? "装船日" : group.date_basis === "delivery_date" ? "交付日" : ""}</span></div>
                <div className="col-start-1 text-sm md:col-auto">{group.port}</div>
                <div className="col-start-1 text-sm text-muted-foreground md:col-auto">{group.orders.length} 张<span className="md:hidden"> PO</span></div>
                <div className="col-start-2 row-start-1 flex items-center justify-end gap-1 md:col-auto md:row-auto">
                  <Button asChild size="sm" variant="ghost" className="text-primary"><Link href={arrangementPath(group.id)}>查看整单</Link></Button>
                  <Button size="icon-sm" variant="outline" aria-label={isOpen ? `收起 ${group.ship} 的 PO` : `展开 ${group.ship} 的 PO`} aria-expanded={isOpen} onClick={() => toggle(key)}>{isOpen ? <ChevronDown /> : <ChevronRight />}</Button>
                </div>
              </div>
              {isOpen && <PoRows orders={group.orders} busy={busy} onAssign={order => openEdit({ kind: "assign", order, groupId: group.id })} onRemove={order => void action(async () => { await removeOrderFromGroup(group.id, order.id); toast.success("PO 已移至未分类，自动扫描会保留此选择"); })} onReclassify={order => void reclassify(order)} onDelete={order => openEdit({ kind: "delete", order, groupId: group.id })} />}
            </div>;
          })}
          {!groups.length && <div className="p-12 text-center text-sm text-muted-foreground">{filtered ? "没有符合筛选的供船订单" : "暂无供船订单；信息完整的 PO 会自动归类"}</div>}
        </div>
      </>}

      <Dialog open={!!edit} onOpenChange={open => { if (!open && !busy) setEdit(null); }}>
        <DialogContent className="max-w-md"><DialogHeader><DialogTitle>{edit?.kind === "assign" ? "手动指定供船订单" : "删除 PO"}</DialogTitle><DialogDescription>{edit?.kind === "assign" ? "人工归类后，自动扫描会保留你的选择。" : `确认删除 ${edit?.order.po_number || `PO #${edit?.order.id}`}？`}</DialogDescription></DialogHeader>
          {edit?.kind === "assign" && <select aria-label="目标供船订单" className={`${selectClass} w-full min-w-0`} value={target} onChange={event => setTarget(event.target.value)}><option value="">选择已有供船订单</option>{data?.arrangements.filter(group => group.can_manage && group.id !== edit.groupId).map(group => <option key={group.id} value={group.id}>{group.ship} · {group.day || "日期待确认"} · {group.port}</option>)}<option value="new">＋ 新建人工供船订单</option></select>}
          {edit?.kind === "assign" && target === "new" && <Input aria-label="供船订单名称" maxLength={200} placeholder="供船订单名称" value={name} onChange={event => setName(event.target.value)} />}
          <div className="flex justify-end gap-2"><Button variant="ghost" disabled={busy} onClick={() => setEdit(null)}>取消</Button><Button disabled={busy || (edit?.kind === "assign" && (!target || (target === "new" && !name.trim())))} onClick={() => void save()}>{busy ? "处理中…" : "确认"}</Button></div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
