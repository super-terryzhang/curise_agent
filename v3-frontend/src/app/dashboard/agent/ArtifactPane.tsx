"use client";

/**
 * ArtifactPane — right-hand workspace surface for chat artifacts.
 *
 * Why this exists: the value the user takes home is usually NOT the chat
 * narration — it's the table, the diff, the generated Excel. Inline
 * artifact rendering inside the thread scrolls past as the conversation
 * grows, burying the user's actual work product. Lifting artifacts into a
 * pinned right-hand pane keeps them in view as the chat continues.
 *
 * Header bar layout (adaptive on artifact count):
 *   - ≤ 4 artifacts: horizontal tab row, one tab per artifact.
 *   - > 4 artifacts: single "🗂 工作面板 (N) ▾" button → vertical
 *     dropdown list (Claude-style "Switch between artifacts"). Tabs
 *     would compress past readability at 5+ entries on a 360px pane.
 *
 * Active-id state lives in `ArtifactSelectorContext` (one level up so
 * inline chips in the chat stream can also drive selection). This
 * component is a pure consumer: it reads `activeId` from context, falls
 * back to "newest" when nothing's selected yet, and calls `selectById`
 * when the user picks a tab / dropdown item.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, LayoutGrid } from "lucide-react";

import type { ChatArtifact } from "@/lib/v3-chat-store";
import { ArtifactPanel } from "@/components/artifacts/ArtifactPanel";
import { cn } from "@/lib/utils";

import { useArtifactSelector } from "@/components/assistant/ArtifactSelectorContext";

// Switch from tab row to dropdown above this artifact count. 4 fits
// comfortably as tabs on a 360px pane; 5 starts looking cramped on
// average Chinese narration lengths (8–14 chars). Empirical, adjust later.
const TAB_THRESHOLD = 4;

interface Props {
  artifacts: ChatArtifact[];
}

/**
 * Pure helper: resolve the currently-displayed artifact given the
 * selector's `activeId` and the live artifacts list.
 *
 * - active id still in list → use it (user's manual pick wins)
 * - active id missing (or null) → fall back to the newest artifact
 *
 * Returns null only when there are zero artifacts. Exported so the
 * unit test can pin this behaviour without rendering React.
 */
export function resolveActiveArtifact(
  artifacts: ChatArtifact[],
  activeId: number | null,
): ChatArtifact | null {
  if (artifacts.length === 0) return null;
  if (activeId != null) {
    const hit = artifacts.find((a) => a.id === activeId);
    if (hit) return hit;
  }
  return artifacts[artifacts.length - 1];
}

export function ArtifactPane({ artifacts }: Props) {
  const selector = useArtifactSelector();
  const activeId = selector?.activeId ?? null;
  const setActiveId = selector?.selectById ?? (() => {});

  const active = useMemo(
    () => resolveActiveArtifact(artifacts, activeId),
    [artifacts, activeId],
  );

  // Auto-promote the newest artifact when one arrives and user hasn't
  // manually picked anything yet. Only fires when activeId is null OR
  // points to something no longer in the list — preserves manual picks
  // across artifact stream events.
  useEffect(() => {
    if (artifacts.length === 0) return;
    const inList = activeId != null && artifacts.some((a) => a.id === activeId);
    if (!inList) {
      setActiveId(artifacts[artifacts.length - 1].id);
    }
  }, [artifacts, activeId, setActiveId]);

  // ── Empty state ────────────────────────────────────────────
  if (artifacts.length === 0) {
    return (
      <div className="grid h-full min-w-0 place-items-center px-4 text-center">
        <div className="max-w-[14rem] space-y-1">
          <div className="text-xs font-medium text-foreground/80">
            工作面板
          </div>
          <div className="break-words text-[11px] text-muted-foreground">
            生成的表格、差异、文件会出现在这里
          </div>
        </div>
      </div>
    );
  }

  // ── Header bar + body ──────────────────────────────────────
  const useDropdown = artifacts.length > TAB_THRESHOLD;
  const activeIndex =
    active != null ? artifacts.findIndex((a) => a.id === active.id) : -1;

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b bg-muted/30 px-3 py-2">
        {/* Active artifact title + position */}
        <div className="flex min-w-0 flex-1 items-baseline gap-2">
          <span className="truncate text-xs font-medium text-foreground">
            {active?.narration || active?.component || "工作面板"}
          </span>
          {artifacts.length > 1 && (
            <span className="shrink-0 text-[10px] text-muted-foreground">
              {activeIndex + 1} / {artifacts.length}
            </span>
          )}
        </div>

        {/* Switcher: tab row (≤ threshold) or dropdown (above) */}
        {useDropdown ? (
          <DropdownSwitcher
            artifacts={artifacts}
            activeId={active?.id ?? null}
            onSelect={setActiveId}
          />
        ) : (
          <TabRow
            artifacts={artifacts}
            activeId={active?.id ?? null}
            onSelect={setActiveId}
          />
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        {active && <ArtifactPanel artifact={active} />}
      </div>
    </div>
  );
}

// ─── Tab row (≤ TAB_THRESHOLD artifacts) ─────────────────────

function TabRow({
  artifacts,
  activeId,
  onSelect,
}: {
  artifacts: ChatArtifact[];
  activeId: number | null;
  onSelect: (id: number) => void;
}) {
  return (
    <div className="flex shrink-0 items-center gap-1 overflow-x-auto">
      {artifacts.map((a) => {
        const selected = a.id === activeId;
        return (
          <button
            key={a.id}
            type="button"
            onClick={() => onSelect(a.id)}
            title={a.narration || a.component}
            className={cn(
              "shrink-0 rounded-md border px-2 py-1 text-[11px] transition-colors",
              selected
                ? "border-foreground/40 bg-background text-foreground"
                : "border-transparent text-muted-foreground hover:bg-background hover:text-foreground",
            )}
          >
            <span className="inline-block max-w-[8rem] truncate align-bottom">
              {a.narration || a.component}
            </span>
          </button>
        );
      })}
    </div>
  );
}

// ─── Dropdown switcher (> TAB_THRESHOLD artifacts) ───────────

function DropdownSwitcher({
  artifacts,
  activeId,
  onSelect,
}: {
  artifacts: ChatArtifact[];
  activeId: number | null;
  onSelect: (id: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // Close on outside click + Escape.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative shrink-0" ref={wrapRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex h-7 items-center gap-1.5 rounded-md border bg-background px-2 text-[11px] hover:bg-muted"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <LayoutGrid className="h-3 w-3" />
        {artifacts.length}
        <ChevronDown className="h-3 w-3" />
      </button>
      {open && (
        <div
          role="listbox"
          aria-label="切换 artifact"
          className="absolute right-0 top-full z-30 mt-1 max-h-[60vh] w-64 overflow-y-auto rounded-lg border bg-popover p-1 shadow-md"
        >
          {artifacts.map((a) => {
            const selected = a.id === activeId;
            return (
              <button
                key={a.id}
                role="option"
                aria-selected={selected}
                onClick={() => {
                  onSelect(a.id);
                  setOpen(false);
                }}
                className={cn(
                  "flex w-full flex-col items-start gap-0.5 rounded-md px-2 py-1.5 text-left text-xs transition-colors",
                  selected
                    ? "bg-accent"
                    : "hover:bg-accent/60",
                )}
              >
                <span className="line-clamp-2 break-words font-medium">
                  {a.narration || a.component}
                </span>
                <span className="text-[10px] text-muted-foreground">
                  {a.component}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
