"use client";

import { useEffect, useState } from "react";
import {
  type ColumnDef,
  type ColumnFiltersState,
  type PaginationState,
  type SortingState,
  type VisibilityState,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  ChevronLeft,
  ChevronRight,
  Search,
  SlidersHorizontal,
} from "lucide-react";

interface DataTableProps<TData, TValue> {
  columns: ColumnDef<TData, TValue>[];
  data: TData[];
  searchKey?: string;
  searchPlaceholder?: string;
  pageSize?: number;
  onRowClick?: (row: TData) => void;
  emptyState?: React.ReactNode;
  toolbar?: React.ReactNode;
  // Server-side pagination
  totalRows?: number;
  onPageChange?: (pageIndex: number, pageSize: number) => void;
  /**
   * Column ids hidden by default. Users can re-enable them via the
   * "列" toolbar button. Use the same string you pass to `accessorKey`
   * (or `id`) on the ColumnDef. The toggle UI is only rendered when this
   * prop is supplied — keeps existing tables that don't opt in unchanged.
   */
  defaultHiddenColumns?: string[];
  /**
   * Optional localStorage key to persist the user's per-table visibility
   * choices across reloads. Without a key, choices reset on page refresh.
   * Pick a stable string per page, e.g. "v3.data.suppliers.cols".
   */
  visibilityStorageKey?: string;
  /** Let the surrounding page own vertical scrolling. */
  scrollMode?: "contained" | "page";
}

export function DataTable<TData, TValue>({
  columns,
  data,
  searchKey,
  searchPlaceholder = "搜索...",
  pageSize = 20,
  onRowClick,
  emptyState,
  toolbar,
  totalRows,
  onPageChange,
  defaultHiddenColumns,
  visibilityStorageKey,
  scrollMode = "contained",
}: DataTableProps<TData, TValue>) {
  const isServerPagination = totalRows != null && onPageChange != null;

  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize,
  });
  // Column visibility: seed from localStorage when a storage key is set,
  // otherwise from the `defaultHiddenColumns` prop. We only use the
  // localStorage value if it's a valid object — guards against the user
  // hand-editing the entry into something junk.
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(
    () => {
      if (typeof window !== "undefined" && visibilityStorageKey) {
        try {
          const raw = window.localStorage.getItem(visibilityStorageKey);
          if (raw) {
            const parsed = JSON.parse(raw);
            if (parsed && typeof parsed === "object") return parsed;
          }
        } catch {
          // ignore — fall through to default
        }
      }
      const initial: VisibilityState = {};
      for (const col of defaultHiddenColumns ?? []) initial[col] = false;
      return initial;
    },
  );

  // Persist visibility changes so the user's "列" picks survive reload.
  useEffect(() => {
    if (typeof window === "undefined" || !visibilityStorageKey) return;
    try {
      window.localStorage.setItem(
        visibilityStorageKey,
        JSON.stringify(columnVisibility),
      );
    } catch {
      // ignore quota / privacy-mode errors
    }
  }, [columnVisibility, visibilityStorageKey]);

  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    ...(isServerPagination
      ? {
          manualPagination: true,
          pageCount: Math.ceil(totalRows / pagination.pageSize),
          onPaginationChange: (updater) => {
            const next =
              typeof updater === "function" ? updater(pagination) : updater;
            setPagination(next);
            onPageChange(next.pageIndex, next.pageSize);
          },
        }
      : {
          getPaginationRowModel: getPaginationRowModel(),
        }),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    onColumnVisibilityChange: setColumnVisibility,
    state: { sorting, columnFilters, pagination, columnVisibility },
  });

  // Only the user-toggleable, hideable columns appear in the picker:
  // skip "actions" (always visible) and any column without a string
  // header (icon-only columns would just show up as blank rows).
  const showVisibilityPicker = !!defaultHiddenColumns;
  const togglableColumns = showVisibilityPicker
    ? table.getAllLeafColumns().filter((col) => {
        if (!col.getCanHide()) return false;
        if (col.id === "actions") return false;
        return typeof col.columnDef.header === "string";
      })
    : [];

  const displayTotal = isServerPagination
    ? totalRows
    : table.getFilteredRowModel().rows.length;

  return (
    <div className={scrollMode === "page" ? "flex flex-col" : "flex flex-col h-full"}>
      {/* Toolbar */}
      {(searchKey || toolbar || showVisibilityPicker) && (
        <div className="flex items-center gap-3 px-4 py-3 shrink-0">
          {searchKey && (
            <div className="relative max-w-xs">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                placeholder={searchPlaceholder}
                value={(table.getColumn(searchKey)?.getFilterValue() as string) ?? ""}
                onChange={(e) =>
                  table.getColumn(searchKey)?.setFilterValue(e.target.value)
                }
                className="pl-9 h-8 text-xs w-56"
              />
            </div>
          )}
          {toolbar}
          {showVisibilityPicker && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  className="ml-auto h-8 gap-1.5 text-xs"
                >
                  <SlidersHorizontal className="h-3.5 w-3.5" />
                  列
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-44">
                <DropdownMenuLabel className="text-xs">显示列</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {togglableColumns.map((col) => (
                  <DropdownMenuCheckboxItem
                    key={col.id}
                    className="text-xs capitalize"
                    checked={col.getIsVisible()}
                    onCheckedChange={(value) => col.toggleVisibility(!!value)}
                    onSelect={(e) => e.preventDefault()}
                  >
                    {String(col.columnDef.header)}
                  </DropdownMenuCheckboxItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      )}

      {/* Table */}
      <div className={scrollMode === "page" ? "overflow-x-auto" : "flex-1 overflow-auto"}>
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id} className="border-border/50 hover:bg-transparent">
                {headerGroup.headers.map((header) => (
                  <TableHead
                    key={header.id}
                    className="text-xs font-medium text-muted-foreground h-9"
                  >
                    {header.isPlaceholder
                      ? null
                      : flexRender(header.column.columnDef.header, header.getContext())}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows?.length ? (
              table.getRowModel().rows.map((row) => (
                <TableRow
                  key={row.id}
                  data-state={row.getIsSelected() && "selected"}
                  className={onRowClick ? "cursor-pointer" : ""}
                  onClick={() => onRowClick?.(row.original)}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id} className="text-xs py-2.5">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell colSpan={columns.length} className="h-32 text-center">
                  {emptyState || (
                    <span className="text-muted-foreground text-sm">暂无数据</span>
                  )}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      {/* Pagination */}
      {table.getPageCount() > 1 && (
        <div className="flex items-center justify-between px-4 py-2 border-t border-border/50 shrink-0">
          <span className="text-xs text-muted-foreground">
            共 {displayTotal} 条
          </span>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
            >
              <ChevronLeft className="h-3.5 w-3.5" />
            </Button>
            <span className="text-xs text-muted-foreground px-2">
              {table.getState().pagination.pageIndex + 1} / {table.getPageCount()}
            </span>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
            >
              <ChevronRight className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
