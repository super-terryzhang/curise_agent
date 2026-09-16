"use client";

/**
 * Folder tree sidebar for the document list page (P3B 2026-06-21,
 * UX overhaul 2026-06-22).
 *
 * Coexists with `TagFilterSidebar` rather than replacing it. Folders are
 * the document's primary location (Dropbox-style); tags remain the
 * orthogonal cross-cutting filter.
 *
 * Selection contract (the parent owns it):
 *   selected = undefined → "show all documents" (no folder filter)
 *   selected = "root"    → "show only unfiled docs"
 *   selected = <id>      → "show only this folder's docs"
 *
 * 2026-06-22 UX upgrades:
 *   - Drag a row from the documents table onto a folder row → move
 *     that document into the folder. Drop on "未分类" moves it to
 *     root. Drop visual highlights on the entire row.
 *   - "新建子文件夹" + "移动到…" now use shadcn Dialog instead of
 *     window.prompt (consistent + mobile-friendly).
 *   - Folder move dialog renders the folder tree itself as targets;
 *     self + descendants are disabled (backend would 400 them).
 */

import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Folder,
  FolderInput,
  FolderOpen,
  FolderPlus,
  Inbox,
  MoreHorizontal,
  Pencil,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { folderColor, FOLDER_COLOR_SWATCHES } from "@/lib/folder-color";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  createFolder,
  deleteFolder,
  moveDocumentToFolder,
  updateFolder,
  type DocumentFolder,
  type FolderScope,
} from "@/lib/documents-api";

// Drag mime — the documents table sets this when its row is dragged.
// Picked deliberately as a fake-app type so other drop targets on the
// page (text inputs, files) won't accidentally pick it up.
export const DOC_DRAG_MIME = "application/x-doc-id";

interface FolderTreeSidebarProps {
  folders: DocumentFolder[];
  selected: FolderScope;
  totalDocCount: number;
  unfiledDocCount?: number;
  onSelect: (next: FolderScope) => void;
  /** Called when the folder list changes (create / rename / delete /
   *  move). Parent re-fetches `listFolders()` plus the document list
   *  (since folder counts may have shifted). */
  onFoldersChanged: () => void;
  /** Optional: called when the user drops a document onto a folder row.
   *  The actual move happens here in the sidebar; this callback lets the
   *  parent re-fetch documents so the moved row disappears from the
   *  current view (if that view's folder filter no longer matches). */
  onDocumentMoved?: () => void;
}

interface TreeNode {
  folder: DocumentFolder;
  children: TreeNode[];
  depth: number;
}

function buildTree(folders: DocumentFolder[]): TreeNode[] {
  const byParent = new Map<number | null, DocumentFolder[]>();
  for (const f of folders) {
    const key = f.parent_folder_id;
    const list = byParent.get(key) ?? [];
    list.push(f);
    byParent.set(key, list);
  }
  for (const list of byParent.values()) {
    list.sort((a, b) => a.name.localeCompare(b.name));
  }
  const make = (parentId: number | null, depth: number): TreeNode[] =>
    (byParent.get(parentId) ?? []).map((f) => ({
      folder: f,
      depth,
      children: make(f.id, depth + 1),
    }));
  return make(null, 0);
}

/**
 * Collect the id-set of `folder` plus everything beneath it. Used by
 * the move dialog to grey out illegal targets (you can't move a folder
 * inside itself or one of its descendants — that creates a cycle).
 */
function descendantIdSet(
  folders: DocumentFolder[],
  rootId: number,
): Set<number> {
  const childrenOf = new Map<number, number[]>();
  for (const f of folders) {
    if (f.parent_folder_id == null) continue;
    const arr = childrenOf.get(f.parent_folder_id) ?? [];
    arr.push(f.id);
    childrenOf.set(f.parent_folder_id, arr);
  }
  const out = new Set<number>([rootId]);
  const stack = [rootId];
  while (stack.length) {
    const id = stack.pop()!;
    for (const c of childrenOf.get(id) ?? []) {
      if (!out.has(c)) {
        out.add(c);
        stack.push(c);
      }
    }
  }
  return out;
}

export function FolderTreeSidebar({
  folders,
  selected,
  totalDocCount,
  unfiledDocCount,
  onSelect,
  onFoldersChanged,
  onDocumentMoved,
}: FolderTreeSidebarProps) {
  // localStorage-persisted expand/collapse so users don't lose tree
  // state every refresh. Keyed by folder id.
  const [expanded, setExpanded] = useState<Set<number>>(() => {
    if (typeof window === "undefined") return new Set();
    try {
      const raw = window.localStorage.getItem("v3.documents.folders.expanded");
      if (!raw) return new Set();
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return new Set(parsed.filter((x) => typeof x === "number"));
    } catch {
      // ignore
    }
    return new Set();
  });

  const persistExpanded = (next: Set<number>) => {
    setExpanded(next);
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem(
          "v3.documents.folders.expanded",
          JSON.stringify(Array.from(next)),
        );
      } catch {
        // quota / privacy mode
      }
    }
  };

  const toggleExpand = (id: number) => {
    const next = new Set(expanded);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    persistExpanded(next);
  };

  const tree = useMemo(() => buildTree(folders), [folders]);

  // Drop handler — single closure shared by every row. Reads the doc
  // id from dataTransfer, calls the move API, then refreshes both the
  // folder counts and the documents list.
  const handleDropDocument = async (targetFolderId: number | null) => {
    const id = Number(
      window.event && "dataTransfer" in (window.event as DragEvent)
        ? (window.event as DragEvent).dataTransfer?.getData(DOC_DRAG_MIME)
        : NaN,
    );
    // Safer path: callers should pass the parsed id explicitly. Kept
    // the window.event fallback for the rare browser still leaning on
    // it. The real call below extracts from the event directly.
    if (Number.isFinite(id) && id > 0) {
      await performMove(id, targetFolderId);
    }
  };
  void handleDropDocument; // The real drop handler lives on each row.

  const performMove = async (docId: number, targetFolderId: number | null) => {
    try {
      await moveDocumentToFolder(docId, targetFolderId);
      onFoldersChanged();
      onDocumentMoved?.();
      toast.success(
        targetFolderId == null
          ? "已移动到「未分类」"
          : `已移动到「${folders.find((f) => f.id === targetFolderId)?.name ?? "目标文件夹"}」`,
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "移动失败");
    }
  };

  return (
    <div className="flex flex-col">
      <div className="flex items-center justify-between px-3 py-2.5">
        <span className="text-xs font-medium text-muted-foreground">文件夹</span>
        <NewFolderInlineButton parentFolderId={null} onCreated={onFoldersChanged} />
      </div>

      {/* Pseudo-nodes: "全部文档" + "未分类". 未分类 is a drop target. */}
      <div className="px-1.5 pb-1">
        <SelectableRow
          icon={<FolderOpen className="h-3.5 w-3.5 text-muted-foreground/70" />}
          label="全部文档"
          count={totalDocCount}
          isSelected={selected === undefined}
          onClick={() => onSelect(undefined)}
          depth={0}
        />
        <SelectableRow
          icon={<Inbox className="h-3.5 w-3.5 text-muted-foreground/70" />}
          label="未分类"
          count={unfiledDocCount}
          isSelected={selected === "root"}
          onClick={() => onSelect("root")}
          depth={0}
          dropTargetFolderId={null}
          onDropDoc={performMove}
        />
      </div>

      <div className="px-1.5 pb-2">
        {tree.length === 0 ? (
          <p className="px-3 py-2 text-[10px] text-muted-foreground/60">
            还没有文件夹，点 + 新建
          </p>
        ) : (
          tree.map((node) => (
            <TreeNodeView
              key={node.folder.id}
              node={node}
              allFolders={folders}
              expanded={expanded}
              onToggleExpand={toggleExpand}
              selected={selected}
              onSelect={onSelect}
              onFoldersChanged={onFoldersChanged}
              onDropDoc={performMove}
            />
          ))
        )}
      </div>
    </div>
  );
}

// ─── Sub-components ───────────────────────────────────────────

function TreeNodeView({
  node,
  allFolders,
  expanded,
  onToggleExpand,
  selected,
  onSelect,
  onFoldersChanged,
  onDropDoc,
}: {
  node: TreeNode;
  allFolders: DocumentFolder[];
  expanded: Set<number>;
  onToggleExpand: (id: number) => void;
  selected: FolderScope;
  onSelect: (next: FolderScope) => void;
  onFoldersChanged: () => void;
  onDropDoc: (docId: number, targetFolderId: number | null) => Promise<void>;
}) {
  const hasChildren = node.children.length > 0;
  const isExpanded = expanded.has(node.folder.id);
  const isSelected =
    typeof selected === "number" && selected === node.folder.id;

  const dot = folderColor(node.folder);
  return (
    <>
      <SelectableRow
        icon={
          // Icon slot always holds the chevron/folder glyph so the tree's
          // expand affordance stays consistent. The per-folder color moves
          // to the leading dot on the label (see labelPrefix below).
          hasChildren ? (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onToggleExpand(node.folder.id);
              }}
              className="text-muted-foreground/70 hover:text-foreground"
              aria-label={isExpanded ? "折叠" : "展开"}
            >
              {isExpanded ? (
                <ChevronDown className="h-3 w-3" />
              ) : (
                <ChevronRight className="h-3 w-3" />
              )}
            </button>
          ) : (
            <Folder className="h-3.5 w-3.5 text-muted-foreground/70" />
          )
        }
        labelPrefix={
          dot ? (
            <span
              className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
              style={{ backgroundColor: dot }}
              aria-hidden
            />
          ) : null
        }
        label={node.folder.name}
        count={node.folder.document_count}
        isSelected={isSelected}
        onClick={() => onSelect(node.folder.id)}
        depth={node.depth}
        dropTargetFolderId={node.folder.id}
        onDropDoc={onDropDoc}
        actions={
          <FolderRowActions
            folder={node.folder}
            allFolders={allFolders}
            onFoldersChanged={onFoldersChanged}
          />
        }
      />
      {isExpanded &&
        node.children.map((child) => (
          <TreeNodeView
            key={child.folder.id}
            node={child}
            allFolders={allFolders}
            expanded={expanded}
            onToggleExpand={onToggleExpand}
            selected={selected}
            onSelect={onSelect}
            onFoldersChanged={onFoldersChanged}
            onDropDoc={onDropDoc}
          />
        ))}
    </>
  );
}

function SelectableRow({
  icon,
  label,
  labelPrefix,
  count,
  isSelected,
  onClick,
  depth,
  actions,
  dropTargetFolderId,
  onDropDoc,
}: {
  icon: React.ReactNode;
  label: string;
  /** Small element rendered flush against the label — used for per-folder
   *  color dots so they sit next to the name instead of replacing the
   *  chevron/folder icon. Optional so pseudo-rows (全部 / 未分类) can skip it. */
  labelPrefix?: React.ReactNode;
  count?: number;
  isSelected: boolean;
  onClick: () => void;
  depth: number;
  actions?: React.ReactNode;
  /** When provided, this row becomes a drop target for document drags
   *  carrying `DOC_DRAG_MIME`. null = move to root (unfiled). */
  dropTargetFolderId?: number | null;
  onDropDoc?: (docId: number, targetFolderId: number | null) => Promise<void>;
}) {
  const [dragOver, setDragOver] = useState(false);
  const isDropTarget = onDropDoc != null && dropTargetFolderId !== undefined;

  return (
    <div
      className={cn(
        "group flex items-center gap-1.5 rounded-md px-1.5 py-1 cursor-pointer transition-colors",
        isSelected
          ? "bg-primary/10 text-primary"
          : "hover:bg-muted/60 text-foreground",
        dragOver && "ring-2 ring-primary bg-primary/5",
      )}
      style={{ paddingLeft: `${depth * 12 + 6}px` }}
      onClick={onClick}
      onDragOver={
        isDropTarget
          ? (e) => {
              // Only react if our specific drag mime is present.
              if (Array.from(e.dataTransfer.types).includes(DOC_DRAG_MIME)) {
                e.preventDefault();
                e.dataTransfer.dropEffect = "move";
                if (!dragOver) setDragOver(true);
              }
            }
          : undefined
      }
      onDragLeave={isDropTarget ? () => setDragOver(false) : undefined}
      onDrop={
        isDropTarget
          ? async (e) => {
              e.preventDefault();
              setDragOver(false);
              const raw = e.dataTransfer.getData(DOC_DRAG_MIME);
              const id = Number(raw);
              if (!Number.isFinite(id) || id <= 0) return;
              await onDropDoc!(id, dropTargetFolderId ?? null);
            }
          : undefined
      }
    >
      <span className="flex h-3.5 w-3.5 items-center justify-center shrink-0">
        {icon}
      </span>
      {labelPrefix ? <span className="shrink-0">{labelPrefix}</span> : null}
      <span className="flex-1 truncate text-xs" title={label}>
        {label}
      </span>
      {count !== undefined && count > 0 && (
        <span className="text-[10px] text-muted-foreground/70 tabular-nums shrink-0">
          {count}
        </span>
      )}
      {actions && (
        <span
          className="opacity-0 group-hover:opacity-100 transition-opacity"
          onClick={(e) => e.stopPropagation()}
        >
          {actions}
        </span>
      )}
    </div>
  );
}

function FolderRowActions({
  folder,
  allFolders,
  onFoldersChanged,
}: {
  folder: DocumentFolder;
  allFolders: DocumentFolder[];
  onFoldersChanged: () => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [newSubOpen, setNewSubOpen] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);

  if (renaming) {
    return (
      <RenameInlineInput
        folder={folder}
        onDone={() => {
          setRenaming(false);
          onFoldersChanged();
        }}
        onCancel={() => setRenaming(false)}
      />
    );
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            className="rounded p-0.5 hover:bg-muted-foreground/10"
            aria-label="文件夹操作"
            onClick={(e) => e.stopPropagation()}
          >
            <MoreHorizontal className="h-3 w-3 text-muted-foreground" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-40">
          <DropdownMenuItem onClick={() => setRenaming(true)}>
            <Pencil className="mr-1.5 h-3 w-3" />
            重命名
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => setNewSubOpen(true)}>
            <FolderPlus className="mr-1.5 h-3 w-3" />
            新建子文件夹
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => setMoveOpen(true)}>
            <FolderInput className="mr-1.5 h-3 w-3" />
            移动到…
          </DropdownMenuItem>

          <DropdownMenuSeparator />
          <DropdownMenuLabel className="text-[10px] font-normal text-muted-foreground/70">
            颜色
          </DropdownMenuLabel>
          {/* Inline swatch grid — click writes the folder's stored color
              (2026-07-22). Every render site of `folderColor(folder)` picks
              it up on the next fetch. `onSelect` preventDefault stops the
              dropdown from auto-closing between rapid picks. */}
          <div className="px-2 pb-1.5 pt-0.5 flex flex-wrap gap-1.5">
            {FOLDER_COLOR_SWATCHES.map((swatch) => (
              <button
                key={swatch}
                type="button"
                aria-label={`更改为 ${swatch}`}
                title={swatch}
                onClick={async (e) => {
                  e.stopPropagation();
                  try {
                    await updateFolder(folder.id, { color: swatch });
                    onFoldersChanged();
                  } catch (err) {
                    toast.error(
                      err instanceof Error ? err.message : "颜色更新失败",
                    );
                  }
                }}
                className={cn(
                  "h-4 w-4 rounded-full border transition-transform hover:scale-110",
                  folder.color === swatch
                    ? "border-foreground/70 ring-1 ring-foreground/30"
                    : "border-border/60",
                )}
                style={{ backgroundColor: swatch }}
              />
            ))}
          </div>
          {folder.color != null && (
            <DropdownMenuItem
              className="text-[11px] text-muted-foreground"
              onClick={async () => {
                try {
                  await updateFolder(folder.id, { color: null });
                  onFoldersChanged();
                } catch (err) {
                  toast.error(
                    err instanceof Error ? err.message : "颜色重置失败",
                  );
                }
              }}
            >
              恢复默认颜色
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />

          <DropdownMenuItem
            className="text-red-600 focus:text-red-600"
            onClick={async () => {
              try {
                await deleteFolder(folder.id);
                toast.success("文件夹已删除");
                onFoldersChanged();
              } catch (err) {
                toast.error(err instanceof Error ? err.message : "删除失败");
              }
            }}
          >
            <Trash2 className="mr-1.5 h-3 w-3" />
            删除
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <NewSubfolderDialog
        open={newSubOpen}
        onOpenChange={setNewSubOpen}
        parentFolder={folder}
        onCreated={onFoldersChanged}
      />
      <MoveFolderDialog
        open={moveOpen}
        onOpenChange={setMoveOpen}
        folder={folder}
        allFolders={allFolders}
        onMoved={onFoldersChanged}
      />
    </>
  );
}

function RenameInlineInput({
  folder,
  onDone,
  onCancel,
}: {
  folder: DocumentFolder;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(folder.name);
  const [saving, setSaving] = useState(false);

  const commit = async () => {
    const next = value.trim();
    if (!next || next === folder.name) {
      onCancel();
      return;
    }
    setSaving(true);
    try {
      await updateFolder(folder.id, { name: next });
      onDone();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "重命名失败");
      onCancel();
    } finally {
      setSaving(false);
    }
  };

  return (
    <input
      autoFocus
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          commit();
        } else if (e.key === "Escape") {
          onCancel();
        }
      }}
      disabled={saving}
      className="rounded border border-border bg-background px-1 py-0 text-xs w-24 focus:outline-none focus:ring-1 focus:ring-primary"
      onClick={(e) => e.stopPropagation()}
    />
  );
}

function NewFolderInlineButton({
  parentFolderId,
  onCreated,
}: {
  parentFolderId: number | null;
  onCreated: () => void;
}) {
  const [creating, setCreating] = useState(false);

  if (creating) {
    return (
      <NewFolderInput
        parentFolderId={parentFolderId}
        onDone={() => {
          setCreating(false);
          onCreated();
        }}
        onCancel={() => setCreating(false)}
      />
    );
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      className="h-5 w-5"
      onClick={() => setCreating(true)}
      title="新建文件夹"
    >
      <FolderPlus className="h-3.5 w-3.5 text-muted-foreground" />
    </Button>
  );
}

function NewFolderInput({
  parentFolderId,
  onDone,
  onCancel,
}: {
  parentFolderId: number | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);

  const commit = async () => {
    const next = value.trim();
    if (!next) {
      onCancel();
      return;
    }
    setSaving(true);
    try {
      await createFolder(next, parentFolderId);
      toast.success(`已创建「${next}」`);
      onDone();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "创建失败");
      onCancel();
    } finally {
      setSaving(false);
    }
  };

  return (
    <input
      autoFocus
      placeholder="新文件夹"
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          commit();
        } else if (e.key === "Escape") {
          onCancel();
        }
      }}
      disabled={saving}
      className="rounded border border-border bg-background px-1 py-0 text-xs w-24 focus:outline-none focus:ring-1 focus:ring-primary"
    />
  );
}

// ─── Dialogs ─────────────────────────────────────────────────

function NewSubfolderDialog({
  open,
  onOpenChange,
  parentFolder,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  parentFolder: DocumentFolder;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);

  const reset = () => {
    setName("");
    setSaving(false);
  };

  const submit = async () => {
    const cleaned = name.trim();
    if (!cleaned) {
      toast.error("文件夹名称不能为空");
      return;
    }
    setSaving(true);
    try {
      await createFolder(cleaned, parentFolder.id);
      toast.success(`已创建「${cleaned}」`);
      onCreated();
      onOpenChange(false);
      reset();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "创建失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent className="max-w-sm" onClick={(e) => e.stopPropagation()}>
        <DialogHeader>
          <DialogTitle>新建子文件夹</DialogTitle>
        </DialogHeader>
        <div className="space-y-2">
          <Label className="text-xs text-muted-foreground">
            位于「{parentFolder.name}」下
          </Label>
          <Input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="子文件夹名称"
            maxLength={200}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                submit();
              }
            }}
            disabled={saving}
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            取消
          </Button>
          <Button onClick={submit} disabled={saving || !name.trim()}>
            创建
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function MoveFolderDialog({
  open,
  onOpenChange,
  folder,
  allFolders,
  onMoved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  folder: DocumentFolder;
  allFolders: DocumentFolder[];
  onMoved: () => void;
}) {
  const [saving, setSaving] = useState(false);

  // Compute illegal targets (self + descendants). Memoize on the folder
  // list since this is O(n) but dialog re-renders shouldn't repeat it.
  const forbidden = useMemo(
    () => descendantIdSet(allFolders, folder.id),
    [allFolders, folder.id],
  );
  const tree = useMemo(() => buildTree(allFolders), [allFolders]);

  const move = async (newParentId: number | null) => {
    if (newParentId === folder.parent_folder_id) {
      onOpenChange(false);
      return;
    }
    setSaving(true);
    try {
      await updateFolder(folder.id, { parent_folder_id: newParentId });
      toast.success(
        newParentId == null
          ? "已移到根目录"
          : `已移动到「${allFolders.find((f) => f.id === newParentId)?.name ?? "目标文件夹"}」`,
      );
      onMoved();
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "移动失败");
    } finally {
      setSaving(false);
    }
  };

  // Render the picker tree. Root is a clickable target at top.
  const renderNode = (node: TreeNode): React.ReactNode => {
    const disabled = forbidden.has(node.folder.id);
    const isCurrent = node.folder.id === folder.parent_folder_id;
    return (
      <div key={node.folder.id}>
        <button
          type="button"
          disabled={disabled || saving}
          onClick={() => move(node.folder.id)}
          className={cn(
            "flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-xs",
            disabled
              ? "cursor-not-allowed opacity-30"
              : "hover:bg-muted/60 cursor-pointer",
            isCurrent && "text-muted-foreground italic",
          )}
          style={{ paddingLeft: `${node.depth * 14 + 8}px` }}
          title={
            disabled
              ? "不能移到自身或自身的子文件夹"
              : isCurrent
                ? "当前所在位置"
                : `移动到「${node.folder.name}」`
          }
        >
          <Folder className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70" />
          <span className="truncate">{node.folder.name}</span>
          {isCurrent && (
            <span className="ml-auto text-[10px] text-muted-foreground/60">
              当前位置
            </span>
          )}
        </button>
        {node.children.map((c) => renderNode(c))}
      </div>
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-md max-h-[70vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <DialogHeader>
          <DialogTitle>把「{folder.name}」移动到…</DialogTitle>
        </DialogHeader>
        <div className="flex-1 min-h-0 overflow-y-auto rounded border border-border/60 p-1">
          {/* Root row */}
          <button
            type="button"
            disabled={folder.parent_folder_id == null || saving}
            onClick={() => move(null)}
            className={cn(
              "flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-xs",
              folder.parent_folder_id == null
                ? "cursor-not-allowed opacity-30 text-muted-foreground italic"
                : "hover:bg-muted/60 cursor-pointer",
            )}
          >
            <FolderOpen className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70" />
            <span className="truncate">根目录</span>
            {folder.parent_folder_id == null && (
              <span className="ml-auto text-[10px] text-muted-foreground/60">
                当前位置
              </span>
            )}
          </button>
          {tree.map((n) => renderNode(n))}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            取消
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
