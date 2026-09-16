"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { FileText, Filter, Folder, FolderInput, Loader2, MoreHorizontal, Plus, Ship, Trash2, X } from "lucide-react";
import {
  deleteDocument,
  listDocuments,
  listFolders,
  listUserTags,
  uploadDocument,
  type DocumentFolder,
  type DocumentSummary,
  type DocumentStatus,
  type FolderScope,
  type UserTagSummary,
} from "@/lib/documents-api";
import { FolderTreeSidebar, DOC_DRAG_MIME } from "@/components/folder-tree-sidebar";
import { TagFilterSidebar } from "@/components/tag-filter-sidebar";
import { moveDocumentToFolder } from "@/lib/documents-api";
import { folderColor } from "@/lib/folder-color";
import {
  getCachedDocumentsList,
  setCachedDocumentsList,
} from "@/lib/documents-cache";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { OracleScanPanel } from "@/components/oracle-scan-panel";
import { FileDropZone } from "@/components/file-drop-zone";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const DOCUMENTS_PAGE_SIZE = 20;

// Monotonic cursor to discard stale fetchDocuments responses when the user
// rapidly switches folders/tags. Older callbacks compare their cursor to the
// current one and bail if they've been superseded. Module-level so it survives
// re-renders but resets on hard refresh (which is fine — no in-flight requests
// then anyway).
let _fetchCursor = 0;

// Single source of truth: status label + dot color. No background pills.
const STATUS_DOT: Record<DocumentStatus, { label: string; dot: string; pulse?: boolean }> = {
  uploaded: { label: "排队中", dot: "bg-muted-foreground/40", pulse: true },
  extracting: { label: "提取中", dot: "bg-muted-foreground/60", pulse: true },
  extracted: { label: "已提取", dot: "bg-foreground/70" },
  error: { label: "失败", dot: "bg-red-500" },
};

// Small colored dot + folder name — mirrors the sidebar's folder colors so
// the eye can trace "which folder" without reading the sidebar every row.
// Root docs (folder=null) render as a muted "未分类" chip. A folder whose id
// no longer resolves in `folders` (dangling FK — folder deleted after this
// list was cached) falls back to "未知文件夹" with a grey dot.
function FolderChip({
  folder,
  danglingFolderId,
}: {
  folder: DocumentFolder | null;
  danglingFolderId?: number | null;
}) {
  const isUnfiled = folder == null && danglingFolderId == null;
  const label = folder
    ? folder.name
    : danglingFolderId != null
      ? "未知文件夹"
      : "未分类";
  const color = folderColor(folder);
  return (
    <span
      className="inline-flex min-w-0 items-center gap-1 text-[10px] text-muted-foreground/70"
      title={label}
    >
      <span
        className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
        style={{
          backgroundColor: color ?? "var(--muted-foreground)",
          opacity: color ? 1 : isUnfiled ? 0.35 : 0.5,
        }}
      />
      <span className="truncate">{label}</span>
    </span>
  );
}

function StatusDot({ status }: { status: DocumentStatus }) {
  const cfg = STATUS_DOT[status];
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <span className="relative inline-flex h-1.5 w-1.5">
        <span className={`relative inline-flex h-1.5 w-1.5 rounded-full ${cfg.dot}`} />
        {cfg.pulse ? (
          <span className={`absolute inset-0 inline-flex animate-ping rounded-full opacity-60 ${cfg.dot}`} />
        ) : null}
      </span>
      <span className="text-foreground/80">{cfg.label}</span>
    </span>
  );
}

const toUTC = (s: string) => s.endsWith("Z") || s.includes("+") ? s : s + "Z";
function formatRelative(iso: string | null) {
  if (!iso) return "—";
  const then = new Date(toUTC(iso)).getTime();
  const now = Date.now();
  const diffMs = now - then;
  const sec = Math.round(diffMs / 1000);
  if (sec < 60) return "刚刚";
  const min = Math.round(sec / 60);
  if (min < 60) return `${min} 分钟前`;
  const hour = Math.round(min / 60);
  if (hour < 24) return `${hour} 小时前`;
  const day = Math.round(hour / 24);
  if (day < 30) return `${day} 天前`;
  return new Date(toUTC(iso)).toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
}

export default function DocumentsPage() {
  const router = useRouter();
  const _initialCache = getCachedDocumentsList();
  const [documents, setDocuments] = useState<DocumentSummary[]>(_initialCache.items ?? []);
  const [total, setTotal] = useState(_initialCache.total);
  // Only show skeleton on the very first load (no cache yet)
  const [loading, setLoading] = useState(_initialCache.items === null);
  // Pagination — how many rows we've fetched into the visible list. When this
  // is below `total` we show a "load more" button. Reset on every filter
  // change so the user always starts from the newest page.
  const [pageOffset, setPageOffset] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  // "Universe" counts for the sidebar — never filtered by the current view.
  // `unfiledCount` is queried separately with folder=root; total universe is
  // derived as sum(folder.document_count) + unfiledCount so a single count
  // query keeps everything consistent.
  const [unfiledCount, setUnfiledCount] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [showUploadDialog, setShowUploadDialog] = useState(false);
  const [isPurchaseOrder, setIsPurchaseOrder] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [pendingDelete, setPendingDelete] = useState<DocumentSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  // Tag filter — empty array = "全部"; multi-select = AND filter on backend.
  const [activeTags, setActiveTags] = useState<string[]>([]);
  const [allUserTags, setAllUserTags] = useState<UserTagSummary[]>([]);
  // Folder filter (P3B 2026-06-21). undefined=all, "root"=unfiled, number=in folder.
  const [activeFolder, setActiveFolder] = useState<FolderScope>(undefined);
  const [folders, setFolders] = useState<DocumentFolder[]>([]);
  const [mobileFilterOpen, setMobileFilterOpen] = useState(false);
  // Multi-select for bulk move/delete (P3B-F5 2026-06-22). Keyed by
  // document id. Clearing happens on: page reload, folder switch, tag
  // change, after a successful bulk op.
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkMoveOpen, setBulkMoveOpen] = useState(false);
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [bulkRunning, setBulkRunning] = useState(false);

  // Fetch a page. mode="reset" replaces the list from offset=0; mode="append"
  // adds the given offset's slice to the end. The bare `fetchDocuments()` call
  // means "reset from 0" — used by every effect that re-derives the visible
  // list (mount, folder switch, tag switch, post-mutation refresh).
  const fetchDocuments = useCallback(
    async (mode: "reset" | "append" = "reset", offset = 0) => {
      const cursor = ++_fetchCursor;
      if (mode === "append") setLoadingMore(true);
      try {
        const result = await listDocuments({
          limit: DOCUMENTS_PAGE_SIZE,
          offset,
          tags: activeTags.length > 0 ? activeTags : undefined,
          folder: activeFolder,
        });
        // A newer fetch started while we were awaiting — discard this one.
        if (cursor !== _fetchCursor) return;
        if (mode === "append") {
          setDocuments((prev) => {
            const merged = [...prev, ...result.items];
            setCachedDocumentsList(merged, result.total);
            return merged;
          });
          setPageOffset(offset + result.items.length);
        } else {
          setCachedDocumentsList(result.items, result.total);
          setDocuments(result.items);
          setPageOffset(result.items.length);
        }
        setTotal(result.total);
      } catch (error) {
        if (cursor === _fetchCursor) {
          toast.error(error instanceof Error ? error.message : "加载文档失败");
        }
      } finally {
        if (cursor === _fetchCursor) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [activeTags, activeFolder],
  );

  const handleLoadMore = useCallback(() => {
    fetchDocuments("append", pageOffset);
  }, [fetchDocuments, pageOffset]);

  const fetchFolders = useCallback(async () => {
    try {
      setFolders(await listFolders());
    } catch {
      // Sidebar is supplementary; silent failure is acceptable.
    }
  }, []);

  // Universe count for「未分类」— cheap (limit=1, we only need the total field).
  // Re-run on mount + after mutations that could shift it (upload / delete /
  // move / bulk ops). No filter args → returns the company-wide root count.
  const fetchUnfiledCount = useCallback(async () => {
    try {
      const result = await listDocuments({ folder: "root", limit: 1, offset: 0 });
      setUnfiledCount(result.total);
    } catch {
      // Sidebar count is supplementary.
    }
  }, []);

  const handleToggleTag = useCallback((tag: string) => {
    setActiveTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag],
    );
  }, []);

  const handleClearTags = useCallback(() => setActiveTags([]), []);

  // Clear selection whenever the visible set of documents may change.
  // Otherwise we'd carry stale ids that aren't in `documents` anymore.
  useEffect(() => {
    setSelectedIds(new Set());
  }, [activeFolder, activeTags]);

  const toggleSelect = useCallback((id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback(() => {
    setSelectedIds((prev) => {
      if (prev.size === documents.length) return new Set();
      return new Set(documents.map((d) => d.id));
    });
  }, [documents]);

  const runBulkMove = useCallback(
    async (targetFolderId: number | null) => {
      const ids = Array.from(selectedIds);
      if (ids.length === 0) return;
      setBulkRunning(true);
      const successes: number[] = [];
      const failures: Array<{ id: number; err: string }> = [];
      // Sequential is fine for ≤50 docs; concurrent risks rate-limits on
      // the rename endpoint. If we ever batch-move 500+ docs we should add
      // a backend bulk endpoint.
      for (const id of ids) {
        try {
          await moveDocumentToFolder(id, targetFolderId);
          successes.push(id);
        } catch (err) {
          failures.push({
            id,
            err: err instanceof Error ? err.message : String(err),
          });
        }
      }
      setBulkRunning(false);
      setBulkMoveOpen(false);
      if (successes.length > 0) {
        toast.success(`已移动 ${successes.length} 个文档`);
      }
      if (failures.length > 0) {
        toast.error(`${failures.length} 个文档移动失败：${failures[0].err}`);
      }
      setSelectedIds(new Set());
      fetchDocuments();
      fetchFolders();
      fetchUnfiledCount();
    },
    [selectedIds, fetchDocuments, fetchFolders, fetchUnfiledCount],
  );

  const runBulkDelete = useCallback(async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    setBulkRunning(true);
    const successes: number[] = [];
    const failures: Array<{ id: number; err: string }> = [];
    for (const id of ids) {
      try {
        const doc = documents.find((d) => d.id === id);
        await deleteDocument(id, Boolean(doc?.linked_order_id));
        successes.push(id);
      } catch (err) {
        failures.push({
          id,
          err: err instanceof Error ? err.message : String(err),
        });
      }
    }
    setBulkRunning(false);
    setBulkDeleteOpen(false);
    if (successes.length > 0) {
      toast.success(`已删除 ${successes.length} 个文档`);
    }
    if (failures.length > 0) {
      toast.error(`${failures.length} 个文档删除失败：${failures[0].err}`);
    }
    setSelectedIds(new Set());
    fetchDocuments();
    fetchFolders();
    fetchUnfiledCount();
  }, [selectedIds, documents, fetchDocuments, fetchFolders, fetchUnfiledCount]);

  // Refresh the universe of available user_tags for the filter chips.
  // Called on mount + after operations that might create/delete tags.
  const fetchUserTags = useCallback(async () => {
    try {
      const tags = await listUserTags();
      setAllUserTags(tags);
    } catch {
      // The filter chip row is supplementary — silent failure is fine.
    }
  }, []);

  useEffect(() => {
    fetchUserTags();
  }, [fetchUserTags]);

  useEffect(() => {
    fetchFolders();
  }, [fetchFolders]);

  useEffect(() => {
    fetchUnfiledCount();
  }, [fetchUnfiledCount]);

  useEffect(() => {
    fetchDocuments();
  }, [fetchDocuments]);

  // Poll while any document is still being extracted, so the list reflects
  // the new "已提取" / "失败" state without the user having to refresh.
  // Stops automatically once nothing is in progress.
  useEffect(() => {
    const inFlight = documents.some(
      (d) => d.status === "uploaded" || d.status === "extracting",
    );
    if (!inFlight) return;
    const id = setInterval(() => {
      fetchDocuments();
    }, 2000);
    return () => clearInterval(id);
  }, [documents, fetchDocuments]);

  const handleUpload = async () => {
    if (!pendingFile) return;
    setUploading(true);
    try {
      const document = await uploadDocument(pendingFile, isPurchaseOrder);
      toast.success(isPurchaseOrder ? "文档已上传，正在识别采购订单" : "文档已上传，正在提取");
      setShowUploadDialog(false);
      setIsPurchaseOrder(false);
      setPendingFile(null);
      router.push(`/dashboard/documents/${document.id}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "上传失败");
    } finally {
      setUploading(false);
    }
  };

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    const target = pendingDelete;
    const force = Boolean(target.linked_order_id);
    setDeleting(true);
    try {
      await deleteDocument(target.id, force);
      setDocuments((prev) => prev.filter((d) => d.id !== target.id));
      setTotal((t) => Math.max(0, t - 1));
      // Refresh folder + unfiled counts — the deleted doc might have been
      // the last one in its folder / at root, and the sidebar totals need
      // to catch up so the「全部文档」 header math stays consistent.
      fetchFolders();
      fetchUnfiledCount();
      toast.success(
        force && target.linked_order_id
          ? `文档已删除，订单 #${target.linked_order_id} 已解除关联`
          : "文档已删除",
      );
      setPendingDelete(null);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除失败");
    } finally {
      setDeleting(false);
    }
  };

  const summary = useMemo(() => {
    const pending = documents.filter(
      (d) => d.doc_type === "purchase_order" && d.status === "extracted" && !d.linked_order_id,
    ).length;
    const linked = documents.filter((d) => Boolean(d.linked_order_id)).length;
    const failed = documents.filter((d) => d.status === "error").length;
    return { pending, linked, failed };
  }, [documents]);

  if (loading) {
    return (
      <div className="mx-auto max-w-6xl space-y-6 px-8 py-10">
        <div className="space-y-2">
          <Skeleton className="h-7 w-32" />
          <Skeleton className="h-4 w-64" />
        </div>
        <Skeleton className="h-[420px] w-full rounded-md" />
      </div>
    );
  }

  // Universe count for the sidebar「全部文档」row. Derived from the folder
  // counts (already batched company-wide by the backend, see
  // folders/service.py:52-57) plus the standalone `unfiledCount` query.
  // Stays stable across folder/tag filter changes — the header still uses
  // `total` for「X 份文档」so it can react to filters as expected.
  const universeCount =
    folders.reduce((s, f) => s + (f.document_count ?? 0), 0) + unfiledCount;
  const sidebarProps = {
    tags: allUserTags,
    selected: activeTags,
    totalDocCount: universeCount,
    onToggle: handleToggleTag,
    onClearAll: handleClearTags,
  };

  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto flex max-w-7xl gap-6 px-4 py-10 md:px-8">
        {/* Sidebar — desktop only; mobile gets a Sheet (toggled below).
            Folders are the document's primary location (top); tags are
            cross-cutting filters (bottom). Both can be active at once. */}
        <aside className="hidden w-56 shrink-0 self-start rounded-md border border-border/60 bg-background md:block divide-y divide-border/60">
          <FolderTreeSidebar
            folders={folders}
            selected={activeFolder}
            totalDocCount={universeCount}
            unfiledDocCount={unfiledCount}
            onSelect={setActiveFolder}
            onFoldersChanged={() => {
              fetchFolders();
              fetchDocuments();
              fetchUnfiledCount();
            }}
            onDocumentMoved={() => {
              // F6 (2026-06-22): after a drop-move, the row may no longer
              // belong in the current folder filter; re-fetch to drop it.
              fetchDocuments();
              fetchFolders();
              fetchUnfiledCount();
            }}
          />
          <TagFilterSidebar {...sidebarProps} />
        </aside>

        <div className="min-w-0 flex-1">
          <OracleScanPanel onUpdated={fetchDocuments} />
          {/* Header — flat, no card, no gradient */}
          <header className="mb-8 flex items-end justify-between gap-3">
            <div className="min-w-0">
              <h1 className="text-2xl font-semibold tracking-tight">文档</h1>
              <p className="mt-1 text-sm text-muted-foreground">
                {total} 份文档
                {summary.pending > 0 ? <> · <span className="text-foreground/80">{summary.pending} 待处理</span></> : null}
                {summary.linked > 0 ? <> · {summary.linked} 已成单</> : null}
                {summary.failed > 0 ? <> · <span className="text-red-600 dark:text-red-400">{summary.failed} 失败</span></> : null}
                {activeTags.length > 0 ? <> · <span className="text-blue-600 dark:text-blue-400">已按 {activeTags.length} 个标签筛选</span></> : null}
              </p>
            </div>
            <div className="flex items-center gap-2">
              {/* Mobile-only filter button — opens the sidebar in a Sheet */}
              <Sheet open={mobileFilterOpen} onOpenChange={setMobileFilterOpen}>
                <SheetTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-9 gap-1.5 md:hidden"
                  >
                    <Filter className="h-4 w-4" />
                    标签
                    {activeTags.length > 0 && (
                      <span className="ml-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-blue-600 px-1 text-[10px] text-white">
                        {activeTags.length}
                      </span>
                    )}
                  </Button>
                </SheetTrigger>
                <SheetContent side="left" className="w-72 p-0">
                  <SheetHeader className="sr-only">
                    <SheetTitle>文件夹与标签</SheetTitle>
                  </SheetHeader>
                  <div className="divide-y divide-border/60">
                    <FolderTreeSidebar
                      folders={folders}
                      selected={activeFolder}
                      totalDocCount={universeCount}
                      unfiledDocCount={unfiledCount}
                      onSelect={(next) => {
                        setActiveFolder(next);
                        setMobileFilterOpen(false);
                      }}
                      onFoldersChanged={() => {
                        fetchFolders();
                        fetchDocuments();
                        fetchUnfiledCount();
                      }}
                    />
                    <TagFilterSidebar {...sidebarProps} />
                  </div>
                </SheetContent>
              </Sheet>
              <Button onClick={() => setShowUploadDialog(true)} className="h-9 gap-1.5">
                <Plus className="h-4 w-4" />
                上传文档
              </Button>
            </div>
          </header>

          {/* List — table, not cards */}
        {documents.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-md border border-dashed border-border/60 py-20 text-center">
            <FileText className="mb-3 h-8 w-8 text-muted-foreground/60" />
            <p className="text-sm font-medium">暂无文档</p>
            <p className="mt-1 text-xs text-muted-foreground">上传 PDF 或 Excel 开始</p>
            <Button
              variant="outline"
              size="sm"
              className="mt-4 h-8"
              onClick={() => setShowUploadDialog(true)}
            >
              上传文档
            </Button>
          </div>
        ) : (
          <div className="rounded-md border border-border/60">
            <Table>
              <TableHeader>
                <TableRow className="border-border/60 hover:bg-transparent">
                  <TableHead className="h-10 w-10 px-2">
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5 cursor-pointer accent-primary"
                      checked={selectedIds.size > 0 && selectedIds.size === documents.length}
                      ref={(el) => {
                        if (el)
                          el.indeterminate =
                            selectedIds.size > 0 && selectedIds.size < documents.length;
                      }}
                      onChange={toggleSelectAll}
                      aria-label="全选"
                    />
                  </TableHead>
                  <TableHead className="h-10 text-xs font-medium text-muted-foreground">名称</TableHead>
                  <TableHead className="h-10 w-20 text-xs font-medium text-muted-foreground">类型</TableHead>
                  <TableHead className="h-10 w-28 text-xs font-medium text-muted-foreground">状态</TableHead>
                  <TableHead className="h-10 w-24 text-xs font-medium text-muted-foreground">订单</TableHead>
                  <TableHead className="h-10 w-32 text-xs font-medium text-muted-foreground">上传者</TableHead>
                  <TableHead className="h-10 w-28 text-xs font-medium text-muted-foreground">上传</TableHead>
                  <TableHead className="h-10 w-12" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {documents.map((document) => (
                  <TableRow
                    key={document.id}
                    // F6 (2026-06-22): rows are drag sources for the
                    // folder sidebar drop targets. Setting DOC_DRAG_MIME
                    // lets the sidebar's onDragOver detect the kind of
                    // drag without matching arbitrary text.
                    draggable
                    onDragStart={(e) => {
                      e.dataTransfer.setData(DOC_DRAG_MIME, String(document.id));
                      e.dataTransfer.effectAllowed = "move";
                    }}
                    className={cn(
                      "cursor-pointer border-border/60 hover:bg-muted/40",
                      selectedIds.has(document.id) && "bg-primary/5",
                    )}
                    onClick={(e) => {
                      // Don't navigate when the click is on the checkbox
                      // cell — that's a select, not a "open detail" intent.
                      if ((e.target as HTMLElement).closest("[data-row-checkbox]")) return;
                      router.push(`/dashboard/documents/${document.id}`);
                    }}
                  >
                    <TableCell className="py-3 px-2" data-row-checkbox>
                      <input
                        type="checkbox"
                        className="h-3.5 w-3.5 cursor-pointer accent-primary"
                        checked={selectedIds.has(document.id)}
                        onChange={() => toggleSelect(document.id)}
                        aria-label={`选择 ${document.display_name || document.filename}`}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </TableCell>
                    <TableCell className="py-3">
                      <div className="flex min-w-0 items-center gap-2.5">
                        <FileText className="h-4 w-4 shrink-0 text-muted-foreground/70" />
                        <div className="min-w-0 flex flex-col">
                          <span className="truncate font-medium">
                            {document.display_name || document.filename}
                          </span>
                          {document.tags?.includes("download:automatic") && <span className="text-[10px] text-blue-600">Oracle · 自动下载</span>}
                          <div className="flex items-center gap-1.5 min-w-0">
                            {document.display_name && document.display_name !== document.filename && (
                              <>
                                <span
                                  className="truncate text-[10px] text-muted-foreground/60"
                                  title={document.filename}
                                >
                                  {document.filename}
                                </span>
                                <span className="text-[10px] text-muted-foreground/40 shrink-0">·</span>
                              </>
                            )}
                            <FolderChip
                              folder={
                                document.folder_id != null
                                  ? folders.find((f) => f.id === document.folder_id) ?? null
                                  : null
                              }
                              danglingFolderId={document.folder_id}
                            />
                          </div>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell className="py-3 text-xs uppercase tracking-wide text-muted-foreground">
                      {document.file_type}
                    </TableCell>
                    <TableCell className="py-3">
                      <StatusDot status={document.status} />
                    </TableCell>
                    <TableCell className="py-3 text-sm text-muted-foreground">
                      {document.linked_order_id ? (
                        <span className="text-foreground/80">#{document.linked_order_id}</span>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                    <TableCell
                      className="py-3 text-xs text-muted-foreground"
                      title={document.uploader_email ?? undefined}
                    >
                      {document.uploader_name || document.uploader_email || "—"}
                    </TableCell>
                    <TableCell className="py-3 text-xs text-muted-foreground">
                      {formatRelative(document.created_at)}
                    </TableCell>
                    <TableCell className="py-3 text-right">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7 text-muted-foreground hover:text-foreground"
                            onClick={(e) => e.stopPropagation()}
                            aria-label="更多操作"
                          >
                            <MoreHorizontal className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className="w-32">
                          <DropdownMenuItem
                            onClick={(e) => {
                              e.stopPropagation();
                              router.push(`/dashboard/documents/${document.id}`);
                            }}
                          >
                            查看详情
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onClick={(e) => {
                              e.stopPropagation();
                              setPendingDelete(document);
                            }}
                            className="text-red-600 focus:text-red-600 dark:text-red-400 dark:focus:text-red-400"
                          >
                            <Trash2 className="mr-2 h-3.5 w-3.5" />
                            删除
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {documents.length < total && (
              <div className="flex justify-center py-4">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleLoadMore}
                  disabled={loadingMore}
                  className="h-8 text-xs"
                >
                  {loadingMore ? (
                    <>
                      <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                      加载中
                    </>
                  ) : (
                    `加载更多（还剩 ${total - documents.length}）`
                  )}
                </Button>
              </div>
            )}
          </div>
        )}
        </div>
      </div>

      {/* Upload dialog */}
      <Dialog open={showUploadDialog} onOpenChange={(open) => { setShowUploadDialog(open); if (!open) { setIsPurchaseOrder(false); setPendingFile(null); } }}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle className="text-lg">上传文档</DialogTitle>
            <DialogDescription>支持 PDF、Excel 和图片（JPG / PNG / WebP），单个文件最大 30 MB</DialogDescription>
          </DialogHeader>
          <FileDropZone onFile={setPendingFile} accept=".pdf,.xlsx,.jpg,.jpeg,.png,.webp" label="拖放文件到此处" disabled={uploading} maxSizeMB={30} />
          <button
            type="button"
            onClick={() => setIsPurchaseOrder((v) => !v)}
            className={`mt-1 flex w-full items-center gap-3 rounded-lg border px-4 py-3 text-left transition-colors ${
              isPurchaseOrder
                ? "border-primary/60 bg-primary/5 text-foreground"
                : "border-border bg-transparent text-muted-foreground hover:border-border/80 hover:text-foreground"
            }`}
          >
            <div className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border transition-colors ${
              isPurchaseOrder ? "border-primary bg-primary" : "border-muted-foreground/40"
            }`}>
              {isPurchaseOrder && (
                <svg viewBox="0 0 10 8" className="h-2.5 w-2.5 fill-none stroke-white stroke-2">
                  <polyline points="1,4 4,7 9,1" />
                </svg>
              )}
            </div>
            <Ship className="h-4 w-4 shrink-0" />
            <div>
              <div className="text-sm font-medium leading-none">这是游轮采购订单</div>
              <div className="mt-0.5 text-xs text-muted-foreground">自动识别订单字段和产品列表</div>
            </div>
          </button>
          <Button
            onClick={handleUpload}
            disabled={!pendingFile || uploading}
            className="mt-1 w-full"
          >
            {uploading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            {uploading ? "上传中..." : "开始上传"}
          </Button>
        </DialogContent>
      </Dialog>

      {/* Delete confirmation */}
      <AlertDialog
        open={Boolean(pendingDelete)}
        onOpenChange={(open) => {
          if (!open && !deleting) setPendingDelete(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除文档</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-sm leading-6">
                <div>
                  确认要删除 <span className="font-medium text-foreground">{pendingDelete?.filename}</span> 吗？此操作不可撤销。
                </div>
                {pendingDelete?.linked_order_id ? (
                  <div className="text-muted-foreground">
                    该文档已生成订单 #{pendingDelete.linked_order_id}。删除后订单会保留，但与此源文档解除关联。
                  </div>
                ) : null}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                confirmDelete();
              }}
              disabled={deleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deleting ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : null}
              {pendingDelete?.linked_order_id ? "强制删除" : "删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* F5 — Bulk action bar (floating, only when selection > 0). */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-full border border-border bg-background shadow-lg flex items-center gap-1 py-1 px-2">
          <span className="text-xs px-2 text-muted-foreground">
            已选 {selectedIds.size} 项
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1.5"
            onClick={() => setBulkMoveOpen(true)}
            disabled={bulkRunning}
          >
            <FolderInput className="h-3.5 w-3.5" />
            移动到…
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1.5 text-destructive hover:text-destructive"
            onClick={() => setBulkDeleteOpen(true)}
            disabled={bulkRunning}
          >
            <Trash2 className="h-3.5 w-3.5" />
            删除
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={() => setSelectedIds(new Set())}
            disabled={bulkRunning}
            aria-label="取消选择"
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}

      {/* F5 — Bulk move dialog. Picks a folder (or root) for ALL selected docs. */}
      <Dialog
        open={bulkMoveOpen}
        onOpenChange={(open) => !bulkRunning && setBulkMoveOpen(open)}
      >
        <DialogContent className="max-w-md max-h-[70vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>移动 {selectedIds.size} 个文档到…</DialogTitle>
          </DialogHeader>
          <div className="flex-1 min-h-0 overflow-y-auto rounded border border-border/60 p-1">
            <button
              type="button"
              onClick={() => runBulkMove(null)}
              disabled={bulkRunning}
              className="flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted/60 cursor-pointer disabled:opacity-50"
            >
              <Folder className="h-3.5 w-3.5 shrink-0 text-muted-foreground/40" />
              <span className="text-muted-foreground">未分类（根目录）</span>
            </button>
            {folders.length === 0 ? (
              <p className="px-3 py-2 text-[11px] text-muted-foreground/60">
                还没有文件夹，先在左侧新建一个
              </p>
            ) : (
              folders.map((f) => (
                <button
                  key={f.id}
                  type="button"
                  onClick={() => runBulkMove(f.id)}
                  disabled={bulkRunning}
                  className="flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted/60 cursor-pointer disabled:opacity-50"
                >
                  <Folder className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70" />
                  <span className="truncate">{f.name}</span>
                </button>
              ))
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setBulkMoveOpen(false)}
              disabled={bulkRunning}
            >
              取消
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* F5 — Bulk delete confirm. */}
      <AlertDialog
        open={bulkDeleteOpen}
        onOpenChange={(open) => !bulkRunning && setBulkDeleteOpen(open)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除 {selectedIds.size} 个文档？</AlertDialogTitle>
            <AlertDialogDescription>
              此操作不可撤销。已生成订单的文档会被强制解除关联，但订单本身保留。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={bulkRunning}>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                runBulkDelete();
              }}
              disabled={bulkRunning}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {bulkRunning ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : null}
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
