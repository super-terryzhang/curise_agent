"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, FileText, RefreshCw, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  PoManagementTable,
  VoyageManagementTable,
} from "@/components/orders/OrderManagementTables";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
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
  buildPoRows,
  buildVoyageRows,
  filterPoRows,
  filterVoyageRows,
  managementFilterOptions,
  paginateRows,
  type ManagementFilters,
  type ManagementStatus,
  type ManagementView,
  type PoManagementRow,
} from "@/lib/order-management-view";
import { deleteOrder } from "@/lib/orders-api";

const selectClass =
  "h-9 rounded-md border border-input bg-background px-3 text-sm text-foreground";
const EMPTY_RESULT: ArrangementsResult = {
  arrangements: [],
  unclassified: [],
  total_orders: 0,
};
const STATUS_OPTIONS: Array<{ value: ManagementStatus | ""; label: string }> = [
  { value: "", label: "全部" },
  { value: "missing_info", label: "需补充信息" },
  { value: "attention", label: "需处理" },
  { value: "processing", label: "处理中" },
  { value: "failed", label: "处理失败" },
  { value: "normal", label: "正常" },
];

function freshFilters(
  overrides: Partial<ManagementFilters> = {},
): ManagementFilters {
  return {
    search: "",
    ship: "",
    port: "",
    status: "",
    from: "",
    to: "",
    unclassifiedOnly: false,
    ...overrides,
  };
}

type Edit = {
  kind: "assign" | "delete";
  order: ArrangementOrder;
  groupId?: number;
};

function Pager({
  page,
  pageCount,
  onChange,
}: {
  page: number;
  pageCount: number;
  onChange: (page: number) => void;
}) {
  return (
    <div className="flex items-center gap-1">
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label="上一页"
        disabled={page <= 1}
        onClick={() => onChange(page - 1)}
      >
        <ChevronLeft />
      </Button>
      <span className="min-w-12 text-center text-xs text-muted-foreground">
        {page} / {pageCount}
      </span>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label="下一页"
        disabled={page >= pageCount}
        onClick={() => onChange(page + 1)}
      >
        <ChevronRight />
      </Button>
    </div>
  );
}

export default function OrdersPage() {
  const router = useRouter();
  const [data, setData] = useState<ArrangementsResult | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<ManagementView>("po");
  const [poFilters, setPoFilters] = useState<ManagementFilters>(() =>
    freshFilters(),
  );
  const [voyageFilters, setVoyageFilters] = useState<ManagementFilters>(() =>
    freshFilters(),
  );
  const [poPage, setPoPage] = useState(1);
  const [voyagePage, setVoyagePage] = useState(1);
  const [poPageSize, setPoPageSize] = useState(50);
  const [voyagePageSize, setVoyagePageSize] = useState(20);
  const [edit, setEdit] = useState<Edit | null>(null);
  const [target, setTarget] = useState("");
  const [name, setName] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setData(await listArrangements());
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const onFocus = () => {
      void refresh();
    };
    window.addEventListener("focus", onFocus);
    const processing = data
      ? [...data.unclassified, ...data.arrangements.flatMap((group) => group.orders)].some(
          (order) =>
            [
              "uploading",
              "pending_template",
              "extracting",
              "extracted",
              "matching",
            ].includes(order.status) ||
            ["pending", "in_progress", "generating"].includes(
              order.inquiry_status || "",
            ),
        )
      : false;
    const timer = processing ? window.setInterval(onFocus, 15000) : undefined;
    return () => {
      window.removeEventListener("focus", onFocus);
      if (timer) window.clearInterval(timer);
    };
  }, [data, refresh]);

  const source = data || EMPTY_RESULT;
  const allPoRows = buildPoRows(source);
  const allVoyageRows = buildVoyageRows(source);
  const filteredPoRows = filterPoRows(allPoRows, poFilters);
  const filteredVoyageRows = filterVoyageRows(allVoyageRows, voyageFilters);
  const poResult = paginateRows(filteredPoRows, poPage, poPageSize);
  const voyageResult = paginateRows(
    filteredVoyageRows,
    voyagePage,
    voyagePageSize,
  );
  const poOptions = managementFilterOptions(allPoRows);
  const voyageOptions = managementFilterOptions(
    allVoyageRows.filter((row) => row.kind === "arrangement"),
  );
  const currentFilters = view === "po" ? poFilters : voyageFilters;
  const currentOptions = view === "po" ? poOptions : voyageOptions;
  const hasFilters = Object.values(currentFilters).some(Boolean);

  function patchCurrentFilter<K extends keyof ManagementFilters>(
    key: K,
    value: ManagementFilters[K],
  ) {
    if (view === "po") {
      setPoFilters((current) => ({ ...current, [key]: value }));
      setPoPage(1);
    } else {
      setVoyageFilters((current) => ({ ...current, [key]: value }));
      setVoyagePage(1);
    }
  }

  function resetCurrentFilters() {
    if (view === "po") {
      setPoFilters(freshFilters());
      setPoPage(1);
    } else {
      setVoyageFilters(freshFilters());
      setVoyagePage(1);
    }
  }

  const action = async (work: () => Promise<void>) => {
    setBusy(true);
    try {
      await work();
      await refresh();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };

  const openEdit = (value: Edit) => {
    setEdit(value);
    setTarget("");
    setName("");
  };

  const reclassify = (order: ArrangementOrder) =>
    action(async () => {
      const result = await classifyOrder(order.id);
      if (result.group_id) toast.success("PO 已自动归类");
      else toast.info(result.reason || "请补充 PO 信息");
    });

  const save = () =>
    action(async () => {
      if (!edit) return;
      if (edit.kind === "assign") {
        if (target === "new") {
          await createOrderGroup({
            name: name.trim(),
            order_ids: [edit.order.id],
            ship_name: edit.order.ship,
          });
        } else {
          await assignOrdersToGroup(Number(target), [edit.order.id]);
        }
        toast.success("已手动归类；后续自动扫描保留此选择");
      } else {
        await deleteOrder(edit.order.id);
        toast.success("PO 已删除");
      }
      setEdit(null);
    });

  const showUnclassified = () => {
    setPoFilters(freshFilters({ unclassifiedOnly: true }));
    setPoPage(1);
    setView("po");
  };

  const removeFromArrangement = (row: PoManagementRow) => {
    if (row.arrangementId === null) return;
    void action(async () => {
      await removeOrderFromGroup(row.arrangementId as number, row.id);
      toast.success("PO 已移至未分类，自动扫描会保留此选择");
    });
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 p-6">
      <div className="grid items-start gap-4 sm:grid-cols-[1fr_auto_1fr]">
        <PageHeader
          title="订单管理"
          description={
            view === "po"
              ? "查看和处理所有供船 PO"
              : "按轮次查看供船订单及处理进度"
          }
        />
        <div
          className="flex rounded-md border bg-muted/30 p-0.5"
          aria-label="订单查看方式"
        >
          <Button
            size="sm"
            variant={view === "po" ? "secondary" : "ghost"}
            aria-pressed={view === "po"}
            onClick={() => setView("po")}
          >
            按 PO
          </Button>
          <Button
            size="sm"
            variant={view === "voyage" ? "secondary" : "ghost"}
            aria-pressed={view === "voyage"}
            onClick={() => setView("voyage")}
          >
            按轮次
          </Button>
        </div>
        <div className="flex justify-start sm:justify-end">
          <Button size="sm" onClick={() => router.push("/dashboard/documents")}>
            <FileText />
            导入 PO
          </Button>
        </div>
      </div>

      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-72 flex-1">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              aria-label={view === "po" ? "搜索 PO" : "搜索轮次"}
              placeholder={
                view === "po"
                  ? "搜索 PO 编号、船名、港口、商品名称等"
                  : "搜索船名、港口"
              }
              className="pl-9"
              value={currentFilters.search}
              onChange={(event) =>
                patchCurrentFilter("search", event.target.value)
              }
            />
          </div>
          <div className="flex items-center gap-1.5">
            <Input
              className="w-36"
              aria-label="开始日期"
              title="装船日开始日期"
              type="date"
              value={currentFilters.from}
              onChange={(event) =>
                patchCurrentFilter("from", event.target.value)
              }
            />
            <span className="text-muted-foreground">—</span>
            <Input
              className="w-36"
              aria-label="结束日期"
              title="装船日结束日期"
              type="date"
              value={currentFilters.to}
              onChange={(event) =>
                patchCurrentFilter("to", event.target.value)
              }
            />
          </div>
          <Button
            variant="outline"
            size="icon"
            aria-label="刷新订单"
            disabled={loading || busy}
            onClick={() => void refresh()}
          >
            <RefreshCw className={loading ? "animate-spin" : ""} />
          </Button>
        </div>

        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs">
          <label className="flex items-center gap-2">
            <span className="text-muted-foreground">船名</span>
            <select
              aria-label="筛选船名"
              className={`${selectClass} min-w-36`}
              value={currentFilters.ship}
              onChange={(event) =>
                patchCurrentFilter("ship", event.target.value)
              }
            >
              <option value="">全部</option>
              {currentOptions.ships.map((ship) => (
                <option key={ship} value={ship}>
                  {ship}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2">
            <span className="text-muted-foreground">目标港口</span>
            <select
              aria-label="筛选目标港口"
              className={`${selectClass} min-w-36`}
              value={currentFilters.port}
              onChange={(event) =>
                patchCurrentFilter("port", event.target.value)
              }
            >
              <option value="">全部</option>
              {currentOptions.ports.map((port) => (
                <option key={port} value={port}>
                  {port}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2">
            <span className="text-muted-foreground">处理状态</span>
            <select
              aria-label="筛选处理状态"
              className={`${selectClass} min-w-36`}
              value={currentFilters.status}
              onChange={(event) =>
                patchCurrentFilter(
                  "status",
                  event.target.value as ManagementStatus | "",
                )
              }
            >
              {STATUS_OPTIONS.map((option) => (
                <option key={option.value || "all"} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          {view === "po" && poFilters.unclassifiedOnly ? (
            <span className="rounded-md bg-amber-50 px-2 py-1.5 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300">
              范围：仅未分配 PO
            </span>
          ) : null}
          <Button
            size="sm"
            variant="ghost"
            disabled={!hasFilters}
            onClick={resetCurrentFilters}
          >
            重置
          </Button>
        </div>
      </div>

      {error ? (
        <div
          role="alert"
          className="rounded-md border border-destructive/30 p-3 text-sm text-destructive"
        >
          {error}
          <Button variant="link" onClick={() => void refresh()}>
            重新加载
          </Button>
        </div>
      ) : null}

      {!data && loading ? (
        <p className="text-sm text-muted-foreground">正在加载订单…</p>
      ) : data ? (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
            <span aria-live="polite">
              {view === "po"
                ? `共 ${poResult.total} 张 PO${poResult.total !== data.total_orders ? `（全部 ${data.total_orders} 张）` : ""}`
                : `共 ${voyageResult.total} 个轮次${filteredVoyageRows.some((row) => row.kind === "unclassified") ? "（含 1 个未分配）" : ""}`}
            </span>
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-2">
                <span>每页显示</span>
                <select
                  aria-label="每页显示数量"
                  className={`${selectClass} h-8`}
                  value={view === "po" ? poPageSize : voyagePageSize}
                  onChange={(event) => {
                    const value = Number(event.target.value);
                    if (view === "po") {
                      setPoPageSize(value);
                      setPoPage(1);
                    } else {
                      setVoyagePageSize(value);
                      setVoyagePage(1);
                    }
                  }}
                >
                  {(view === "po" ? [20, 50, 100] : [10, 20, 50]).map(
                    (size) => (
                      <option key={size} value={size}>
                        {size}
                      </option>
                    ),
                  )}
                </select>
              </label>
              {view === "po" ? (
                <Pager
                  page={poResult.page}
                  pageCount={poResult.pageCount}
                  onChange={setPoPage}
                />
              ) : (
                <Pager
                  page={voyageResult.page}
                  pageCount={voyageResult.pageCount}
                  onChange={setVoyagePage}
                />
              )}
            </div>
          </div>

          {view === "po" ? (
            <PoManagementTable
              rows={poResult.items}
              busy={busy}
              onAssign={(row) =>
                openEdit({
                  kind: "assign",
                  order: row.order,
                  groupId: row.arrangementId || undefined,
                })
              }
              onRemove={removeFromArrangement}
              onReclassify={(row) => void reclassify(row.order)}
              onDelete={(row) =>
                openEdit({
                  kind: "delete",
                  order: row.order,
                  groupId: row.arrangementId || undefined,
                })
              }
            />
          ) : (
            <VoyageManagementTable
              rows={voyageResult.items}
              onShowUnclassified={showUnclassified}
            />
          )}
        </>
      ) : null}

      <Dialog
        open={Boolean(edit)}
        onOpenChange={(open) => {
          if (!open && !busy) setEdit(null);
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {edit?.kind === "assign" ? "手动指定供船订单" : "删除 PO"}
            </DialogTitle>
            <DialogDescription>
              {edit?.kind === "assign"
                ? "人工归类后，自动扫描会保留你的选择。"
                : `确认删除 ${edit?.order.po_number || `PO #${edit?.order.id}`}？`}
            </DialogDescription>
          </DialogHeader>
          {edit?.kind === "assign" ? (
            <select
              aria-label="目标供船订单"
              className={`${selectClass} w-full min-w-0`}
              value={target}
              onChange={(event) => setTarget(event.target.value)}
            >
              <option value="">选择已有供船订单</option>
              {data?.arrangements
                .filter(
                  (group) => group.can_manage && group.id !== edit.groupId,
                )
                .map((group) => (
                  <option key={group.id} value={group.id}>
                    {group.ship} · {group.day || "日期待确认"} · {group.port}
                  </option>
                ))}
              <option value="new">＋ 新建人工供船订单</option>
            </select>
          ) : null}
          {edit?.kind === "assign" && target === "new" ? (
            <Input
              aria-label="供船订单名称"
              maxLength={200}
              placeholder="供船订单名称"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          ) : null}
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => setEdit(null)}
            >
              取消
            </Button>
            <Button
              disabled={
                busy ||
                (edit?.kind === "assign" &&
                  (!target || (target === "new" && !name.trim())))
              }
              onClick={() => void save()}
            >
              {busy ? "处理中…" : "确认"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
