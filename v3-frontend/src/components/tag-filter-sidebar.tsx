"use client";

/**
 * TagFilterSidebar — multi-select tag filter panel.
 *
 * Renders the user's full tag inventory with per-tag document counts.
 * Click a row to toggle its inclusion in the AND-filter set; selected
 * tags get a filled blue background. A search box at the top filters
 * the visible rows when the universe gets large.
 *
 * The component is a pure controlled component: all state lives in the
 * parent (the documents page). Embed it as a desktop aside (always
 * visible at md+) AND inside a Sheet for mobile (toggled by an icon
 * button) — the parent page renders both wrappers around this same
 * component, so behavior stays identical across breakpoints.
 */

import { useMemo, useState } from "react";
import { Search, Tag as TagIcon, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import type { UserTagSummary } from "@/lib/documents-api";

export function TagFilterSidebar({
  tags,
  selected,
  totalDocCount,
  onToggle,
  onClearAll,
}: {
  tags: UserTagSummary[];
  selected: string[];
  totalDocCount: number;
  onToggle: (name: string) => void;
  onClearAll: () => void;
}) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return tags;
    return tags.filter((t) => t.name.toLowerCase().includes(q));
  }, [tags, query]);

  const hasSelection = selected.length > 0;

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="border-b border-border/60 px-4 pt-4 pb-3">
        <div className="flex items-baseline justify-between">
          <h2 className="text-[13px] font-semibold tracking-tight">标签</h2>
          <span className="text-[11px] text-muted-foreground tabular-nums">
            {tags.length} 个
          </span>
        </div>
        {hasSelection && (
          <button
            type="button"
            onClick={onClearAll}
            className="mt-2 inline-flex items-center gap-1 text-[11px] text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
          >
            <X className="h-3 w-3" />
            清除筛选 ({selected.length})
          </button>
        )}
      </div>

      {/* Search (only when there are enough tags to be worth searching) */}
      {tags.length > 8 && (
        <div className="border-b border-border/60 px-3 py-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/60" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索标签..."
              className="h-8 pl-8 text-xs"
            />
          </div>
        </div>
      )}

      {/* "All" pseudo-row */}
      <div className="px-2 pt-2">
        <button
          type="button"
          onClick={onClearAll}
          className={cn(
            "flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-xs transition-colors",
            !hasSelection
              ? "bg-foreground/5 font-medium text-foreground"
              : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
          )}
        >
          <span className="inline-flex items-center gap-2">
            <TagIcon className="h-3 w-3" />
            全部
          </span>
          <span className="text-[10px] text-muted-foreground tabular-nums">
            {totalDocCount}
          </span>
        </button>
      </div>

      {/* Tag list */}
      <ul className="flex-1 overflow-y-auto px-2 pb-3 pt-1 space-y-0.5">
        {filtered.length === 0 ? (
          <li className="px-2.5 py-3 text-center text-[11px] text-muted-foreground">
            {query ? "没有匹配的标签" : "还没有标签"}
          </li>
        ) : (
          filtered.map((t) => {
            const isOn = selected.includes(t.name);
            return (
              <li key={t.name}>
                <button
                  type="button"
                  onClick={() => onToggle(t.name)}
                  className={cn(
                    "flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-xs transition-colors",
                    isOn
                      ? "bg-blue-600 text-white shadow-sm"
                      : "text-foreground/80 hover:bg-muted/60",
                  )}
                >
                  <span className="flex min-w-0 items-center gap-2">
                    {/* Checkmark indicator (filled when selected) */}
                    <span
                      className={cn(
                        "flex h-3 w-3 shrink-0 items-center justify-center rounded-[3px] border transition-colors",
                        isOn
                          ? "border-white/80 bg-white/20"
                          : "border-foreground/30",
                      )}
                    >
                      {isOn && (
                        <svg
                          viewBox="0 0 8 8"
                          className="h-2 w-2 text-white"
                          aria-hidden
                        >
                          <path
                            d="M1.5 4.2L3 5.7L6.5 2"
                            stroke="currentColor"
                            strokeWidth="1.5"
                            fill="none"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        </svg>
                      )}
                    </span>
                    <span className="truncate" title={t.name}>
                      {t.name}
                    </span>
                  </span>
                  <span
                    className={cn(
                      "ml-2 shrink-0 text-[10px] tabular-nums",
                      isOn ? "text-white/70" : "text-muted-foreground",
                    )}
                  >
                    {t.count}
                  </span>
                </button>
              </li>
            );
          })
        )}
      </ul>

      {/* Footer hint */}
      <div className="border-t border-border/60 px-4 py-2">
        <p className="text-[10px] leading-4 text-muted-foreground">
          {hasSelection
            ? "需匹配所有选中标签"
            : "在文档详情页可添加新标签"}
        </p>
      </div>
    </div>
  );
}
