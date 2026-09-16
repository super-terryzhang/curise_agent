"use client";

/**
 * AssistantSidebar — floating right-side AI chat panel, mounted on
 * every dashboard page except `/dashboard/agent` itself (the full-page
 * workspace already owns the chat surface).
 *
 * UX model — drawn from Cursor / GitHub Copilot Chat / Intercom Fin:
 *   - Default collapsed: a slim 28px tab pinned to the right edge is
 *     the only visible affordance. Users on a fresh browser see the
 *     tab + a one-time onboarding hint (not implemented v1 — log a
 *     follow-up).
 *   - Cmd+/ (macOS) / Ctrl+/ (Win/Linux) toggles the panel. Esc
 *     collapses while focus is inside.
 *   - When expanded: 420px wide, slides over the page (does not push
 *     content), backed by `position: fixed`. The page underneath
 *     stays interactive — users can scroll a data table while the
 *     assistant streams an answer.
 *   - Mobile (<1024px) is handled separately via `<Sheet>` in D1;
 *     this component focuses on the desktop affordance.
 *
 * Why fixed-overlay, not push-content:
 *   B2B data pages (orders, products, suppliers) already have wide
 *   tables that suffer when 30%+ of horizontal space disappears. The
 *   user opened the assistant to ASK about the table — they don't
 *   want the table to vanish while they type. Overlay preserves the
 *   "I can still see what I'm asking about" mental model.
 *
 * Why no resizable width in v1:
 *   The agent page's resizable panes already exist for users who
 *   want a wide workspace. The sidebar's value proposition is
 *   "always-available, predictable size, doesn't fight the page" —
 *   resize is anti-goal here. Revisit if user feedback says otherwise.
 *
 * State sharing:
 *   The Thread inside the sidebar is rendered by `<SharedThread />`
 *   which reads from the same `AssistantProvider` runtime as the
 *   full-page workspace. One SSE subscription, one message buffer,
 *   identical UX. See AssistantProvider docs for the why.
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ChevronRight,
  ExternalLink,
  MessageSquarePlus,
  Sparkles,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetTitle,
} from "@/components/ui/sheet";
import { useAssistantChat } from "./AssistantProvider";
import { SharedThread } from "./SharedThread";

// Width of the expanded sidebar in pixels. Matches the right pane of
// /dashboard/agent's default split for visual consistency when the
// user navigates back and forth. Kept fixed in v1 — see file header.
const SIDEBAR_WIDTH = 420;

// Width of the collapsed tab (always visible, right edge).
const COLLAPSED_TAB_WIDTH = 28;

// localStorage key for persisting open/closed state across page nav
// and reloads. Persisting open-state would surprise first-time
// visitors (they'd see a panel they didn't intend), so we default
// to closed and only restore once the user has toggled at least
// once — captured by the presence of the key.
const STORAGE_KEY = "assistant-sidebar-open";

function readPersistedOpen(): boolean {
  if (typeof window === "undefined") return false;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw === "1";
  } catch {
    // localStorage can throw in private-mode Safari etc. — default
    // to closed rather than crash the layout.
    return false;
  }
}

function writePersistedOpen(open: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, open ? "1" : "0");
  } catch {
    // ignore
  }
}

// Track whether we're below the `lg` breakpoint (1024px). Drives the
// "render desktop aside OR mobile Sheet" branch — we must NEVER mount
// both at the same time because the Sheet (Radix Dialog) renders a
// page-wide modal overlay that traps focus and darkens the screen
// regardless of which CSS classes hide its visual content. `lg:hidden`
// on the Sheet's panel only hides the panel, NOT the overlay.
// (Bug fix 2026-05-29: shipped initial version where the Sheet mounted
// on desktop too and blocked all input the moment the sidebar opened.)
function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(false);
  useEffect(() => {
    const mql = window.matchMedia("(max-width: 1023.98px)");
    const update = () => setIsMobile(mql.matches);
    update();
    // `addEventListener` is the modern API; old Safari needs
    // `addListener`. matchMedia objects expose both.
    if (mql.addEventListener) {
      mql.addEventListener("change", update);
      return () => mql.removeEventListener("change", update);
    }
    mql.addListener(update);
    return () => mql.removeListener(update);
  }, []);
  return isMobile;
}

export function AssistantSidebar() {
  const router = useRouter();
  const chat = useAssistantChat();
  const isMobile = useIsMobile();
  // Start closed regardless of persisted state, then hydrate after
  // mount to avoid SSR/CSR mismatch (Next.js Hydration warning).
  const [open, setOpenState] = useState(false);
  useEffect(() => {
    setOpenState(readPersistedOpen());
  }, []);

  const setOpen = useCallback((nextOrUpdater: boolean | ((prev: boolean) => boolean)) => {
    setOpenState((prev) => {
      const next =
        typeof nextOrUpdater === "function"
          ? (nextOrUpdater as (p: boolean) => boolean)(prev)
          : nextOrUpdater;
      writePersistedOpen(next);
      return next;
    });
  }, []);

  // Cmd+/ (mac) / Ctrl+/ (others) global toggle. We attach to the
  // document so the shortcut works regardless of which page element
  // currently has focus (table cell, button, etc.). The handler is
  // narrow: it ignores plain "/" without modifier so users can type
  // a slash in any input without triggering the panel.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "/") {
        e.preventDefault();
        setOpen((v) => !v);
      } else if (e.key === "Escape" && open) {
        // Only swallow Escape when sidebar is open AND focus is
        // somewhere inside it — otherwise stealing Escape would
        // break dialogs / dropdowns on the page underneath.
        const sidebarEl = document.getElementById("assistant-sidebar-panel");
        if (sidebarEl && sidebarEl.contains(document.activeElement)) {
          e.preventDefault();
          setOpen(false);
        }
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open]);

  // Pop open whenever an in-page button fires `assistant:openAndSend`.
  // The Provider's matching listener takes care of actually sending the
  // message — we only handle the visibility flip here.
  useEffect(() => {
    const handler = () => setOpen(true);
    window.addEventListener("assistant:openAndSend", handler);
    return () => window.removeEventListener("assistant:openAndSend", handler);
  }, [setOpen]);

  const handleOpenInWorkspace = useCallback(() => {
    const href = chat.sessionId
      ? `/dashboard/workbench/ai?session=${chat.sessionId}`
      : "/dashboard/workbench/ai";
    router.push(href);
    setOpen(false);
  }, [chat.sessionId, router]);

  const handleNewConversation = useCallback(() => {
    chat.resetSession();
  }, [chat]);

  const panelBody = (
    <>
      {/* Header */}
      <header className="flex h-12 shrink-0 items-center gap-1 border-b border-border px-3">
        <Sparkles className="h-3.5 w-3.5 text-muted-foreground" />
        <h2 className="text-sm font-medium">AI 助手</h2>
        {chat.sending && (
          <span className="ml-2 text-[10px] text-muted-foreground">
            生成中…
          </span>
        )}
        <div className="ml-auto flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            onClick={handleNewConversation}
            title="新对话"
            aria-label="新对话"
            className="h-7 w-7 text-muted-foreground hover:text-foreground"
          >
            <MessageSquarePlus className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={handleOpenInWorkspace}
            title="在 AI 交流中打开"
            aria-label="在 AI 交流中打开"
            className="h-7 w-7 text-muted-foreground hover:text-foreground"
          >
            <ExternalLink className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setOpen(false)}
            title="收起 (Esc)"
            aria-label="收起"
            className="h-7 w-7 text-muted-foreground hover:text-foreground"
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      </header>

      {/* Thread — same render as /dashboard/agent. textOnlyComposer
          hides the file-attach button (sidebar lacks the wide
          drag-drop target the agent page provides). */}
      <div className="flex flex-1 min-h-0 flex-col">
        <SharedThread
          emptyState={<SidebarEmptyState />}
          textOnlyComposer
        />
      </div>
    </>
  );

  return (
    <>
      {/* Collapsed tab — always present at the right edge on desktop.
          Mobile uses the Sheet's natural open/close UX (close on
          backdrop / swipe) and doesn't need a persistent tab. */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title={open ? "收起 AI 助手 (Esc)" : "打开 AI 助手 (⌘/)"}
        aria-label={open ? "收起 AI 助手" : "打开 AI 助手"}
        aria-expanded={open}
        className="fixed right-0 top-1/2 z-40 hidden h-20 -translate-y-1/2 items-center justify-center rounded-l-md border border-r-0 border-border bg-background shadow-md transition-colors hover:bg-muted lg:flex"
        style={{ width: COLLAPSED_TAB_WIDTH }}
      >
        {open ? (
          <ChevronRight className="h-4 w-4 text-muted-foreground" />
        ) : (
          <Sparkles className="h-4 w-4 text-foreground" />
        )}
      </button>

      {/* Mobile entry — bottom-right FAB so it doesn't fight the
          existing left-nav hamburger. Tap → opens Sheet from right. */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title="打开 AI 助手"
        aria-label="打开 AI 助手"
        className="fixed bottom-5 right-5 z-40 flex h-11 w-11 items-center justify-center rounded-full border border-border bg-background shadow-lg transition-colors hover:bg-muted lg:hidden"
      >
        <Sparkles className="h-4 w-4 text-foreground" />
      </button>

      {/* Desktop panel — sliding from the right edge. The transform
          (not display:none) keeps the Thread mounted across toggle
          cycles so the SSE subscription isn't torn down each time
          and message scroll position survives. */}
      <aside
        id="assistant-sidebar-panel"
        aria-hidden={!open}
        aria-label="AI 助手"
        className="fixed right-0 top-0 z-30 hidden h-screen flex-col border-l border-border bg-background shadow-2xl transition-transform duration-200 ease-out lg:flex"
        style={{
          width: SIDEBAR_WIDTH,
          transform: open
            ? `translateX(0)`
            : `translateX(${SIDEBAR_WIDTH}px)`,
        }}
      >
        {panelBody}
      </aside>

      {/* Mobile panel — full-screen Sheet from right. ONLY mounted
          when the viewport is below `lg` (1024px). Mounting it on
          desktop would render its modal overlay (Radix Dialog) on top
          of the page even though the panel itself is hidden by
          `lg:hidden`, blocking all input under it. The `isMobile`
          guard makes the choice mutually exclusive with the desktop
          aside above. */}
      {isMobile && (
        <Sheet
          open={open}
          onOpenChange={(v) => {
            if (!v) setOpen(false);
          }}
        >
          <SheetContent
            side="right"
            className="flex w-full max-w-full flex-col p-0"
          >
            <SheetTitle className="sr-only">AI 助手</SheetTitle>
            {panelBody}
          </SheetContent>
        </Sheet>
      )}
    </>
  );
}

function SidebarEmptyState() {
  return (
    <div className="mx-auto max-w-sm py-8 px-4 text-center">
      <div className="mb-3 inline-flex h-10 w-10 items-center justify-center rounded-full bg-muted">
        <Sparkles className="h-4 w-4 text-muted-foreground" />
      </div>
      <h3 className="mb-1 text-sm font-medium">AI 助手在这</h3>
      <p className="text-xs text-muted-foreground">
        随时问问题，AI 知道你现在在看哪一页。
        <br />
        快捷键 <kbd className="rounded border bg-muted px-1 py-0.5 text-[10px]">⌘/</kbd> 唤起 ·
        <kbd className="ml-1 rounded border bg-muted px-1 py-0.5 text-[10px]">Esc</kbd> 收起
      </p>
    </div>
  );
}
