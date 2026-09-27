"use client";

import Link from "next/link";
import { MoreHorizontal } from "lucide-react";
import { PortResolutionBadge } from "@/components/orders/PortResolutionReview";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  weekdayLabel,
  type ManagementStatus,
  type PoManagementRow,
  type StatusPresentation,
  type VoyageManagementRow,
} from "@/lib/order-management-view";
import { formatBusinessDateTime } from "@/lib/order-workspace-view";

const STATUS_STYLES: Record<ManagementStatus, string> = {
  missing_info:
    "bg-amber-50 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300",
  failed: "bg-red-50 text-red-700 dark:bg-red-950/60 dark:text-red-300",
  processing:
    "bg-blue-50 text-blue-700 dark:bg-blue-950/60 dark:text-blue-300",
  attention:
    "bg-orange-50 text-orange-700 dark:bg-orange-950/60 dark:text-orange-300",
  normal:
    "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300",
};

const STATUS_DOTS: Record<ManagementStatus, string> = {
  missing_info: "bg-amber-500",
  failed: "bg-red-500",
  processing: "bg-blue-500",
  attention: "bg-orange-500",
  normal: "bg-emerald-600",
};

function StatusChip({ status }: { status: StatusPresentation }) {
  return (
    <span
      className={`inline-flex max-w-full items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium ${STATUS_STYLES[status.code]}`}
    >
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${STATUS_DOTS[status.code]}`}
      />
      <span className="truncate" title={status.label}>
        {status.label}
      </span>
    </span>
  );
}

interface PoManagementTableProps {
  rows: PoManagementRow[];
  busy: boolean;
  onAssign: (row: PoManagementRow) => void;
  onRemove: (row: PoManagementRow) => void;
  onReclassify: (row: PoManagementRow) => void;
  onDelete: (row: PoManagementRow) => void;
}

export function canRemovePo(row: PoManagementRow): boolean {
  return !row.unclassified && row.arrangementId !== null;
}

export function PoManagementTable({
  rows,
  busy,
  onAssign,
  onRemove,
  onReclassify,
  onDelete,
}: PoManagementTableProps) {
  return (
    <div className="min-h-0 flex-1 overflow-auto rounded-lg border">
      <table className="w-full min-w-[1100px] text-sm">
        <thead className="sticky top-0 z-10 bg-muted/70 text-left text-xs text-muted-foreground backdrop-blur">
          <tr>
            <th className="px-4 py-3 font-medium">PO 编号</th>
            <th className="px-4 py-3 font-medium">进入系统时间</th>
            <th className="px-4 py-3 font-medium">船名</th>
            <th className="px-4 py-3 font-medium">装船日期</th>
            <th className="px-4 py-3 font-medium">目标港口</th>
            <th className="px-4 py-3 font-medium">商品数</th>
            <th className="px-4 py-3 font-medium">处理状态</th>
            <th className="px-4 py-3 text-right font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.id}
              data-testid="po-management-row"
              className="border-t bg-background hover:bg-muted/20"
            >
              <td className="px-4 py-3 font-medium">
                <Link
                  className="hover:text-primary hover:underline"
                  href={`/dashboard/orders/${row.id}`}
                >
                  {row.poNumber}
                </Link>
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-xs tabular-nums text-muted-foreground">
                {formatBusinessDateTime(row.order.created_at)}
              </td>
              <td className="max-w-44 px-4 py-3">
                <span className="block truncate" title={row.ship || undefined}>
                  {row.ship || "—"}
                </span>
              </td>
              <td className="whitespace-nowrap px-4 py-3">{row.day || "—"}</td>
              <td className="max-w-40 px-4 py-3">
                <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                  <span className="truncate" title={row.port || undefined}>
                    {row.port || "—"}
                  </span>
                  <PortResolutionBadge state={row.order.port_resolution} />
                </div>
              </td>
              <td className="px-4 py-3">{row.productCount}</td>
              <td className="max-w-52 px-4 py-3">
                <StatusChip status={row.status} />
              </td>
              <td className="px-3 py-2 text-right">
                <div className="flex items-center justify-end gap-1">
                  <Button asChild size="sm" variant="link" className="px-2">
                    <Link href={`/dashboard/orders/${row.id}`}>查看</Link>
                  </Button>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        disabled={busy}
                        aria-label={`${row.poNumber} 更多操作`}
                      >
                        <MoreHorizontal />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onSelect={() => onAssign(row)}>
                        手动指定供船订单
                      </DropdownMenuItem>
                      {canRemovePo(row) ? (
                        <DropdownMenuItem onSelect={() => onRemove(row)}>
                          移至未分类
                        </DropdownMenuItem>
                      ) : null}
                      <DropdownMenuItem onSelect={() => onReclassify(row)}>
                        按 PO 信息重新自动归类
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        variant="destructive"
                        onSelect={() => onDelete(row)}
                      >
                        删除 PO
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              </td>
            </tr>
          ))}
          {!rows.length ? (
            <tr>
              <td
                colSpan={8}
                className="h-32 px-4 text-center text-sm text-muted-foreground"
              >
                没有符合筛选的 PO
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

interface VoyageManagementTableProps {
  rows: VoyageManagementRow[];
  onShowUnclassified: () => void;
}

export function VoyageManagementTable({
  rows,
  onShowUnclassified,
}: VoyageManagementTableProps) {
  return (
    <div className="min-h-0 flex-1 overflow-auto rounded-lg border">
      <table className="w-full min-w-[920px] text-sm">
        <thead className="sticky top-0 z-10 bg-muted/70 text-left text-xs text-muted-foreground backdrop-blur">
          <tr>
            <th className="px-4 py-3 font-medium">装船日期</th>
            <th className="px-4 py-3 font-medium">船名</th>
            <th className="px-4 py-3 font-medium">目标港口</th>
            <th className="px-4 py-3 font-medium">PO 数</th>
            <th className="px-4 py-3 font-medium">商品数</th>
            <th className="px-4 py-3 font-medium">处理状态</th>
            <th className="px-4 py-3 text-right font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const unclassified = row.kind === "unclassified";
            return (
              <tr
                key={row.id}
                data-testid="voyage-management-row"
                className={`border-t hover:bg-muted/20 ${
                  unclassified
                    ? "bg-amber-50/60 dark:bg-amber-950/20"
                    : "bg-background"
                }`}
              >
                <td className="whitespace-nowrap px-4 py-3">
                  {row.day ? (
                    <>
                      <span className="block">{row.day}</span>
                      <span className="text-xs text-muted-foreground">
                        {weekdayLabel(row.day)}
                      </span>
                    </>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="max-w-52 px-4 py-3 font-medium">
                  <span className="block truncate" title={row.ship}>
                    {row.ship}
                  </span>
                </td>
                <td className="max-w-44 px-4 py-3">
                  <span className="block truncate" title={row.port || undefined}>
                    {row.port || "—"}
                  </span>
                  {row.pendingPortReviewCount > 0 ? (
                    <span className="mt-1 block text-xs text-amber-700 dark:text-amber-300">
                      {row.pendingPortReviewCount} 个 PO 港口待确认
                    </span>
                  ) : null}
                </td>
                <td className="px-4 py-3">{row.poCount}</td>
                <td className="px-4 py-3">{row.productCount}</td>
                <td className="max-w-52 px-4 py-3">
                  <StatusChip status={row.status} />
                </td>
                <td className="px-4 py-3 text-right">
                  {unclassified ? (
                    <Button
                      size="sm"
                      variant="link"
                      className="px-2"
                      onClick={onShowUnclassified}
                    >
                      查看未分配 PO
                    </Button>
                  ) : (
                    <Button asChild size="sm" variant="link" className="px-2">
                      <Link
                        href={`/dashboard/orders/arrangements/${row.arrangementId}`}
                      >
                        查看
                      </Link>
                    </Button>
                  )}
                </td>
              </tr>
            );
          })}
          {!rows.length ? (
            <tr>
              <td
                colSpan={7}
                className="h-32 px-4 text-center text-sm text-muted-foreground"
              >
                没有符合筛选的轮次
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}
