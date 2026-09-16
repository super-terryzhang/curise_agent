"use client";

/**
 * OrderGroupsSection — R7 / Felix 2026-06-12 "合单" UX.
 *
 * Display-level only: each Order keeps its identity, the group is a
 * collapsible visual aggregation matching Felix's description
 * ("三个订单是同一天一条船的，能合到一天去 ... 标签左边有个加号或倒三角").
 *
 * Self-contained — designed to mount above the existing DataTable on
 * /dashboard/orders without restructuring it. Talks to /api/order-groups
 * directly; the parent only needs to pass the order list (for the create
 * modal's selector) and an onChange to refresh orders after mutations
 * (since group_id changes on the underlying orders).
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronDown, ChevronRight, FileSpreadsheet, Plus, Pencil, Trash2, UserPlus, X, MoreHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { toast } from "sonner";

import {
  autoGroupOrders,
  assignOrdersToGroup,
  createOrderGroup,
  deleteOrderGroup,
  generateInquiryForGroup,
  listOrderGroups,
  removeOrderFromGroup,
  updateOrderGroup,
  type OrderGroup,
} from "@/lib/order-groups-api";
import { getUser } from "@/lib/auth";
import { listOrders } from "@/lib/orders-api";
import type { OrderListItem } from "@/lib/orders-api";

interface Props {
  /** All orders currently in scope (so the create modal can let the user
   *  pick which ones to fold in). Parent should pass the same list it
   *  renders below. */
  orders: OrderListItem[];
  /** Called after any mutation that changes orders' group_id, so the
   *  parent can re-fetch and re-render the table. */
  onOrdersChanged: () => void;
}

// localStorage key per group to remember expand state across nav. Scoped
// by group_id so two groups don't share a key.
const expandKey = (groupId: number) => `order-group:expanded:${groupId}`;

export function OrderGroupsSection({ orders, onOrdersChanged }: Props) {
  const router = useRouter();
  const [groups, setGroups] = useState<OrderGroup[]>([]);
  const [groupMembers, setGroupMembers] = useState<OrderListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [autoBusy, setAutoBusy] = useState(false);
  const [canAuto, setCanAuto] = useState(false);
  const [autoSkipped, setAutoSkipped] = useState<{ order_id: number; reason: string }[]>([]);
  useEffect(() => { setCanAuto(["admin", "superadmin"].includes(getUser()?.role || "")); }, []);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

  // Create modal
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createShip, setCreateShip] = useState("");
  const [createLoadingDate, setCreateLoadingDate] = useState("");
  const [createSelectedIds, setCreateSelectedIds] = useState<Set<number>>(
    new Set(),
  );
  const [submitting, setSubmitting] = useState(false);

  // Edit / delete state
  const [editTarget, setEditTarget] = useState<OrderGroup | null>(null);
  const [editName, setEditName] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<OrderGroup | null>(null);

  // G1 (2026-06-22): "append orders to existing group" dialog. Backend
  // already supports `POST /api/order-groups/{id}/orders` — we just lacked
  // the UI hook. assignTarget is the group we're adding into.
  const [assignTarget, setAssignTarget] = useState<OrderGroup | null>(null);
  const [assignSelectedIds, setAssignSelectedIds] = useState<Set<number>>(
    new Set(),
  );

  // ─── Load ──────────────────────────────────────────────────

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listOrderGroups();
      // Groups span pages. Fetch the authenticated member list independently
      // from the ungrouped table's current page/filter.
      const members: OrderListItem[] = [];
      if (data.length > 0) {
        let offset = 0;
        while (true) {
          const page = await listOrders({ limit: 100, offset });
          members.push(...page.items.filter(o => o.group_id != null));
          offset += page.items.length;
          if (offset >= page.total || page.items.length === 0) break;
        }
      }
      setGroupMembers(Array.from(new Map(members.map(o => [o.id, o])).values()));
      setGroups([...data].sort((a, b) => (b.loading_date || b.name.match(/\d{4}-\d{2}-\d{2}/)?.[0] || "").localeCompare(a.loading_date || a.name.match(/\d{4}-\d{2}-\d{2}/)?.[0] || "")));
      // Hydrate expanded state from localStorage now we know the ids.
      const next: Record<number, boolean> = {};
      for (const g of data) {
        const stored = localStorage.getItem(expandKey(g.id));
        next[g.id] = stored === "1";
      }
      setExpanded(next);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "加载分组失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, orders]);

  // ─── Actions ───────────────────────────────────────────────

  const runAutoGroup = async () => {
    setAutoBusy(true);
    try {
      const result = await autoGroupOrders();
      setAutoSkipped(result.skipped);
      await refresh();
      onOrdersChanged();
      toast.success(`新建 ${result.created_groups} 个分组，归入 ${result.assigned_orders} 条订单`);
    } catch (error) { toast.error(error instanceof Error ? error.message : "自动归组失败"); }
    finally { setAutoBusy(false); }
  };

  const toggleExpand = (id: number) => {
    setExpanded((prev) => {
      const next = { ...prev, [id]: !prev[id] };
      localStorage.setItem(expandKey(id), next[id] ? "1" : "0");
      return next;
    });
  };

  const toggleAll = () => {
    const open = !groups.every(g => expanded[g.id]);
    setExpanded(Object.fromEntries(groups.map(g => [g.id, open])));
    for (const g of groups) localStorage.setItem(expandKey(g.id), open ? "1" : "0");
  };

  const openCreate = () => {
    setCreateName("");
    setCreateShip("");
    setCreateLoadingDate("");
    setCreateSelectedIds(new Set());
    setCreateOpen(true);
  };

  const submitCreate = async () => {
    const name = createName.trim();
    if (!name) {
      toast.error("请输入分组名称");
      return;
    }
    setSubmitting(true);
    try {
      await createOrderGroup({
        name,
        ship_name: createShip.trim() || null,
        loading_date: createLoadingDate.trim() || null,
        order_ids:
          createSelectedIds.size > 0 ? Array.from(createSelectedIds) : undefined,
      });
      setCreateOpen(false);
      await refresh();
      // If we assigned orders, refresh parent table so rows show under group.
      if (createSelectedIds.size > 0) onOrdersChanged();
      toast.success("分组已创建");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  const submitEdit = async () => {
    if (!editTarget) return;
    const name = editName.trim();
    if (!name) {
      toast.error("分组名称不能为空");
      return;
    }
    try {
      await updateOrderGroup(editTarget.id, { name });
      setEditTarget(null);
      await refresh();
      toast.success("已保存");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "保存失败");
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteOrderGroup(deleteTarget.id);
      setDeleteTarget(null);
      await refresh();
      // Orders' group_id flips to NULL — parent must re-render to move them
      // back into the ungrouped section.
      onOrdersChanged();
      toast.success("分组已删除");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "删除失败");
    }
  };

  const openAssign = (g: OrderGroup) => {
    setAssignTarget(g);
    setAssignSelectedIds(new Set());
  };

  const submitAssign = async () => {
    if (!assignTarget || assignSelectedIds.size === 0) return;
    try {
      await assignOrdersToGroup(assignTarget.id, Array.from(assignSelectedIds));
      setAssignTarget(null);
      await refresh();
      onOrdersChanged();
      toast.success(`已添加 ${assignSelectedIds.size} 个订单`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "添加失败");
    }
  };

  const generateMergedInquiry = async (g: OrderGroup) => {
    try {
      const result = await generateInquiryForGroup(g.id);
      toast.success(
        `已开始为分组「${g.name}」生成合并询价，正在跳转…`,
      );
      // The merged inquiry uses anchor_order_id as the inquiry's order_id;
      // navigate to that order's detail page where the existing inquiry
      // UI (SSE stream, preview, download per supplier) just works.
      router.push(`/dashboard/orders/${result.anchor_order_id}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "生成失败");
    }
  };

  const removeFromGroup = async (groupId: number, orderId: number) => {
    try {
      await removeOrderFromGroup(groupId, orderId);
      await refresh();
      onOrdersChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "移除失败");
    }
  };

  // ─── Lookups ────────────────────────────────────────────────

  // Map of orders by group_id so we can render group children inline.
  const ordersByGroup = new Map<number, OrderListItem[]>();
  for (const o of groupMembers) {
    const gid = (o as OrderListItem & { group_id?: number | null }).group_id;
    if (gid != null) {
      const arr = ordersByGroup.get(gid) ?? [];
      arr.push(o);
      ordersByGroup.set(gid, arr);
    }
  }

  // Orders eligible for assignment in the create modal — only ungrouped
  // so the user doesn't accidentally yank an order from another group.
  const ungroupedOrders = orders.filter(
    (o) => (o as OrderListItem & { group_id?: number | null }).group_id == null,
  );

  // ─── Render ────────────────────────────────────────────────

  // Don't render anything if there are no groups AND nothing to group —
  // keep the page chrome quiet on first-time use until the user has data.
  if (!loading && groups.length === 0 && ungroupedOrders.length === 0) {
    return null;
  }

  return (
    <div className="mb-4 rounded-lg border bg-card p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">订单分组 <span className="ml-1 font-normal text-muted-foreground">{groups.length}</span></h2>
        <div className="flex flex-wrap gap-2">
        {groups.length > 0 && <Button size="sm" variant="ghost" onClick={toggleAll}>{groups.every(g => expanded[g.id]) ? "全部收起" : "全部展开"}</Button>}
        {canAuto && <Button size="sm" variant="outline" onClick={runAutoGroup} disabled={loading || autoBusy}>{autoBusy ? "正在归组…" : "自动归组现有订单"}</Button>}
        <Button
          size="sm"
          variant="outline"
          onClick={openCreate}
          disabled={loading || ungroupedOrders.length === 0}
        >
          <Plus className="mr-1 h-4 w-4" />
          新建分组
        </Button>
        </div>
      </div>
      <details className="mb-3 text-xs text-muted-foreground"><summary className="cursor-pointer">自动归组规则</summary><p className="mt-2">同一邮轮、同一天、同一目标港口。优先装船日，缺失时按交付日单独归组；手动调整会保留。</p></details>
      {autoSkipped.length > 0 && <details className="mb-3 text-xs text-muted-foreground"><summary className="cursor-pointer">{autoSkipped.length} 条订单需补充或核对信息</summary><ul className="mt-2 space-y-1">{autoSkipped.map(item => <li key={item.order_id}><button className="underline" onClick={() => router.push(`/dashboard/orders/${item.order_id}`)}>订单 #{item.order_id}</button>：{item.reason}</li>)}</ul></details>}

      {loading ? (
        <p className="text-xs text-muted-foreground">加载中…</p>
      ) : groups.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          还没有分组。同一天一条船的订单可以打包进同一个分组方便查看。
        </p>
      ) : (
        <ul className="space-y-2">
          {groups.map((g) => {
            const isOpen = expanded[g.id] ?? false;
            const groupOrders = ordersByGroup.get(g.id) ?? [];
            return (
              <li key={g.id} className="rounded border bg-background">
                <div className="flex items-center justify-between gap-2 px-3 py-2">
                  <button
                    type="button"
                    onClick={() => toggleExpand(g.id)}
                    aria-expanded={isOpen}
                    className="flex min-w-0 flex-1 items-center gap-2 py-1 text-left"
                  >
                    {isOpen ? (
                      <ChevronDown className="h-4 w-4 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="h-4 w-4 text-muted-foreground" />
                    )}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium" title={g.name}>{g.name}</span>
                      {g.ship_name && !g.name.toLowerCase().includes(g.ship_name.toLowerCase()) && <span className="text-xs text-muted-foreground">{g.ship_name} </span>}
                      {g.loading_date && !g.name.includes(g.loading_date) && <span className="text-xs text-muted-foreground">{g.loading_date}</span>}
                    </span>
                    <span className="shrink-0 rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground">{g.order_count} 单</span>
                  </button>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild><Button size="icon" variant="ghost" className="h-8 w-8 shrink-0" aria-label={`分组操作：${g.name}`}><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem disabled={groupOrders.length === 0 || !g.can_generate_inquiry} onSelect={() => generateMergedInquiry(g)}><FileSpreadsheet className="h-4 w-4" />生成合并询价</DropdownMenuItem>
                      <DropdownMenuItem disabled={ungroupedOrders.length === 0 || !g.can_manage} onSelect={() => openAssign(g)}><UserPlus className="h-4 w-4" />加入订单</DropdownMenuItem>
                      <DropdownMenuItem disabled={!g.can_manage} onSelect={() => { setEditTarget(g); setEditName(g.name); }}><Pencil className="h-4 w-4" />重命名</DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem variant="destructive" disabled={!g.can_manage} onSelect={() => setDeleteTarget(g)}><Trash2 className="h-4 w-4" />删除分组（不删订单）</DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
                {isOpen && (
                  <div className="border-t bg-muted/30 px-3 py-2">
                    {groupOrders.length === 0 ? (
                      <p className="text-xs text-muted-foreground">
                        该分组下没有订单（可能不在当前筛选条件里）
                      </p>
                    ) : (
                      <ul className="space-y-1">
                        {groupOrders.map((o) => (
                          <li
                            key={o.id}
                            className="group flex items-center justify-between gap-3 rounded px-2 text-sm hover:bg-background"
                          >
                            <button type="button" onClick={() => router.push(`/dashboard/orders/${o.id}`)} className="flex min-w-0 flex-1 items-center gap-3 py-2 text-left">
                              <span className="min-w-0 flex-1 truncate font-mono text-xs">{o.order_metadata?.po_number || o.filename}</span>
                              <span className="hidden text-xs text-muted-foreground sm:inline">{o.country_name || ""}</span>
                              <span className="shrink-0 text-xs text-muted-foreground">{o.product_count} 个产品</span>
                            </button>
                            <button
                              type="button"
                              onClick={() => removeFromGroup(g.id, o.id)}
                              className="p-1 text-xs text-muted-foreground hover:text-destructive focus-visible:opacity-100 sm:opacity-0 sm:group-hover:opacity-100"
                              title="从分组移除"
                            >
                              <X className="h-3.5 w-3.5" />
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {/* ─── Create modal ────────────────────────────────────── */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>新建分组</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="grp-name">分组名称 *</Label>
              <Input
                id="grp-name"
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                placeholder="例：MILLENNIUM 6/15"
                autoFocus
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="grp-ship">船名（选填）</Label>
                <Input
                  id="grp-ship"
                  value={createShip}
                  onChange={(e) => setCreateShip(e.target.value)}
                  placeholder="CELEBRITY MILLENNIUM"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="grp-date">装船日（选填）</Label>
                <Input
                  id="grp-date"
                  value={createLoadingDate}
                  onChange={(e) => setCreateLoadingDate(e.target.value)}
                  placeholder="2026-06-15"
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>把哪些订单放进来（可选，之后也可以加）</Label>
              {ungroupedOrders.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  当前没有未分组的订单。
                </p>
              ) : (
                <div className="max-h-60 space-y-1 overflow-y-auto rounded border p-2">
                  {ungroupedOrders.map((o) => {
                    const checked = createSelectedIds.has(o.id);
                    return (
                      <label
                        key={o.id}
                        className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 hover:bg-muted"
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(e) => {
                            const v = e.target.checked;
                            setCreateSelectedIds((prev) => {
                              const next = new Set(prev);
                              if (v) next.add(o.id);
                              else next.delete(o.id);
                              return next;
                            });
                          }}
                          className="h-4 w-4 cursor-pointer accent-primary"
                        />
                        <span className="font-mono text-xs text-muted-foreground">
                          {o.order_metadata?.po_number || "-"}
                        </span>
                        <span className="flex-1 truncate text-sm">
                          {o.order_metadata?.ship_name || "-"}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {o.country_name || "-"}
                        </span>
                      </label>
                    );
                  })}
                </div>
              )}
              {createSelectedIds.size > 0 && (
                <p className="text-xs text-muted-foreground">
                  已选 {createSelectedIds.size} 个订单
                </p>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setCreateOpen(false)}
              disabled={submitting}
            >
              取消
            </Button>
            <Button onClick={submitCreate} disabled={submitting}>
              创建
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ─── Edit name dialog ────────────────────────────────── */}
      <Dialog
        open={editTarget != null}
        onOpenChange={(open) => !open && setEditTarget(null)}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>重命名分组</DialogTitle>
          </DialogHeader>
          <Input
            value={editName}
            onChange={(e) => setEditName(e.target.value)}
            autoFocus
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditTarget(null)}>
              取消
            </Button>
            <Button onClick={submitEdit}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ─── Assign-to-existing-group dialog (G1 2026-06-22) ── */}
      <Dialog
        open={assignTarget != null}
        onOpenChange={(open) => !open && setAssignTarget(null)}
      >
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>把订单加入「{assignTarget?.name}」</DialogTitle>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label>选择要加入的订单（仅未分组的）</Label>
            {ungroupedOrders.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                当前没有未分组的订单。
              </p>
            ) : (
              <div className="max-h-60 space-y-1 overflow-y-auto rounded border p-2">
                {ungroupedOrders.map((o) => {
                  const checked = assignSelectedIds.has(o.id);
                  return (
                    <label
                      key={o.id}
                      className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 hover:bg-muted"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(e) => {
                          const v = e.target.checked;
                          setAssignSelectedIds((prev) => {
                            const next = new Set(prev);
                            if (v) next.add(o.id);
                            else next.delete(o.id);
                            return next;
                          });
                        }}
                        className="h-4 w-4 cursor-pointer accent-primary"
                      />
                      <span className="font-mono text-xs text-muted-foreground">
                        {o.order_metadata?.po_number || "-"}
                      </span>
                      <span className="flex-1 truncate text-sm">
                        {o.order_metadata?.ship_name || "-"}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {o.country_name || "-"}
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
            {assignSelectedIds.size > 0 && (
              <p className="text-xs text-muted-foreground">
                已选 {assignSelectedIds.size} 个
              </p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAssignTarget(null)}>
              取消
            </Button>
            <Button onClick={submitAssign} disabled={assignSelectedIds.size === 0}>
              加入
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ─── Delete confirm ──────────────────────────────────── */}
      <AlertDialog
        open={deleteTarget != null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除分组「{deleteTarget?.name}」</AlertDialogTitle>
            <AlertDialogDescription>
              只删分组，里面的订单不会被删除，只是回到"未分组"列表。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={confirmDelete}>删除</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
