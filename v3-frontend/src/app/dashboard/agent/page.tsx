/**
 * /dashboard/agent — AI workspace.
 *
 * Refactored 2026-05-29: the chat runtime now lives at
 * `dashboard/layout.tsx` via `AssistantProvider`. This page is a pure
 * consumer — it pulls `chat` from context, renders the agent-page
 * chrome (header, side panels, drag-drop), and delegates the actual
 * Thread + Composer render to `<SharedThread />` which the floating
 * sidebar also reuses. Single source of truth = one SSE subscription,
 * one message buffer, identical UX wherever Thread is mounted.
 *
 * What lives here (page-specific chrome):
 *   - Three-column resizable layout (SessionList · Thread · ArtifactPane)
 *   - Header bar (model picker, new-session, panel toggles)
 *   - Drag-and-drop overlay (file gets attached via context)
 *   - URL ↔ session sync (`?session=<id>` deep link)
 *   - EmptyState with quick-start tiles
 *   - Mobile single-column fallback with inline artifacts
 *
 * What's elsewhere:
 *   - `components/assistant/AssistantProvider.tsx` — chat + runtime
 *   - `components/assistant/SharedThread.tsx` — Thread render + composer
 *     + all message/part/approval/tool-UI renderers
 *   - `components/assistant/AssistantSidebar.tsx` — floating sidebar
 *     (mounted in layout, hidden on this page)
 */

"use client";

import {
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Check,
  Cpu,
  PanelLeft,
  PanelRight,
  Plus,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useAssistantChat } from "@/components/assistant/AssistantProvider";
import { SharedThread } from "@/components/assistant/SharedThread";
import type { V3ModelChoice } from "@/lib/v3-chat-api";
import { ArtifactPane } from "./ArtifactPane";
import { TILES, type Tile } from "./tiles";
import { useResizable } from "./use-resizable";
import { SessionList } from "./SessionList";

export default function AgentV2Page() {
  return (
    <Suspense
      fallback={
        <div className="p-6 text-sm text-muted-foreground">加载中…</div>
      }
    >
      <AgentV2Inner />
    </Suspense>
  );
}

function AgentV2Inner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const chat = useAssistantChat();

  const [showSidebar, setShowSidebar] = useState(true);
  const [showArtifacts, setShowArtifacts] = useState(true);

  // URL ↔ session sync. The provider starts with `null` so the user's
  // last session (or a fresh one) is what loads on first visit; this
  // effect catches `?session=abc` deep-links and routes them into the
  // shared chat. Only the agent page touches the URL — other surfaces
  // (the sidebar) inherit whatever's currently loaded.
  useEffect(() => {
    const id = searchParams?.get("session") ?? null;
    if (id && id !== chat.sessionId) {
      chat.loadSession(id).catch((e) => {
        toast.error(e instanceof Error ? e.message : "会话加载失败");
      });
    }
  }, [searchParams, chat]);

  const sidebar = useResizable({
    storageKey: "agent:sidebar-width",
    defaultWidth: 240,
    min: 180,
    max: 420,
    edge: "left",
  });
  const artifacts = useResizable({
    storageKey: "agent:artifacts-width",
    defaultWidth: 480,
    min: 320,
    max: 900,
    edge: "right",
  });

  // ─── Tile click ─────────────────────────────────────────
  const handleTileClick = useCallback(
    async (tile: Tile) => {
      if (tile.id === "upload") {
        router.push("/dashboard/workbench/product-upload");
        return;
      }
      if (!tile.prompt) {
        document.querySelector<HTMLTextAreaElement>("textarea")?.focus();
        return;
      }
      try {
        await chat.sendMessage(tile.prompt);
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "发送失败");
      }
    },
    [chat, router],
  );

  const handleSelectSession = useCallback(
    (id: string) => {
      chat.loadSession(id);
      router.replace(`/dashboard/workbench/ai?session=${id}`, { scroll: false });
    },
    [chat, router],
  );

  const handleNewSession = useCallback(() => {
    chat.resetSession();
    router.replace("/dashboard/workbench/ai", { scroll: false });
  }, [chat, router]);

  const headerBar = (
    <header className="flex h-12 shrink-0 items-center gap-1 border-b px-3">
      <Button
        variant="ghost"
        size="icon"
        onClick={() => setShowSidebar((v) => !v)}
        className="h-8 w-8 text-muted-foreground hover:text-foreground"
        aria-label={showSidebar ? "收起会话列表" : "展开会话列表"}
        title={showSidebar ? "收起会话列表" : "展开会话列表"}
      >
        <PanelLeft className="h-4 w-4" />
      </Button>
      <h1 className="ml-1 text-sm font-medium flex items-center gap-1.5">
        <Sparkles className="h-3.5 w-3.5 text-muted-foreground" />
        AI 交流
      </h1>
      <div className="ml-auto flex items-center gap-2">
        <ModelPicker
          available={chat.availableModels}
          current={chat.model}
          onPick={chat.setModel}
          disabled={chat.sending}
        />
        <Button
          variant="ghost"
          size="sm"
          onClick={handleNewSession}
          className="h-8 px-2.5 text-xs gap-1.5"
        >
          <Plus className="h-3.5 w-3.5" />
          新对话
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setShowArtifacts((v) => !v)}
          className="h-8 w-8 text-muted-foreground hover:text-foreground"
          aria-label={showArtifacts ? "收起工作面板" : "展开工作面板"}
          title={showArtifacts ? "收起工作面板" : "展开工作面板"}
        >
          <PanelRight className="h-4 w-4" />
        </Button>
      </div>
    </header>
  );

  // Inline artifact tail — mobile-only fallback when the right pane is
  // hidden (the desktop layout owns the artifact pane explicitly).
  const inlineArtifactsTail = chat.artifacts.length > 0 && (
    <div className="space-y-3 px-4 py-2">
      {chat.artifacts.map((a) => (
        <div
          key={a.id}
          className="rounded-md border bg-background p-2 text-xs"
        >
          <div className="mb-1 font-medium">🖼️ {a.narration}</div>
          <div className="text-[10px] text-muted-foreground">{a.component}</div>
        </div>
      ))}
    </div>
  );

  const threadColumn = (inlineArtifacts: boolean) => (
    <main className="flex h-full min-w-0 flex-1 flex-col">
      {headerBar}
      <SharedThread
        emptyState={<EmptyState onTileClick={handleTileClick} />}
        textOnlyComposer
      />
      {inlineArtifacts && inlineArtifactsTail}
    </main>
  );

  return (
    <div className="relative h-[calc(100vh-4rem)]">

      {/* Desktop: 3-column resizable layout */}
      <div className="hidden h-full lg:flex">
        {showSidebar && (
          <>
            <aside
              className="h-full shrink-0 border-r bg-muted/30 overflow-hidden"
              style={{ width: sidebar.width }}
            >
              <div className="h-full [&>div]:!w-full [&>div]:!border-r-0">
                <SessionList
                  activeSessionId={chat.sessionId}
                  onSelectSession={handleSelectSession}
                  onNewSession={handleNewSession}
                />
              </div>
            </aside>
            <DragHandle
              onMouseDown={sidebar.startDrag}
              ariaLabel="拖动调整侧边栏宽度"
            />
          </>
        )}

        <div className="flex h-full min-w-0 flex-1 flex-col">
          {threadColumn(false)}
        </div>

        {showArtifacts && (
          <>
            <DragHandle
              onMouseDown={artifacts.startDrag}
              ariaLabel="拖动调整工作面板宽度"
            />
            <aside
              className="h-full shrink-0 border-l overflow-hidden"
              style={{ width: artifacts.width }}
            >
              <ArtifactPane artifacts={chat.artifacts} />
            </aside>
          </>
        )}
      </div>

      {/* Mobile: single column */}
      <div className="h-full lg:hidden">{threadColumn(true)}</div>
    </div>
  );
}

// ─── Drag handle ────────────────────────────────────────────

function DragHandle({
  onMouseDown,
  ariaLabel,
}: {
  onMouseDown: (e: React.MouseEvent) => void;
  ariaLabel: string;
}) {
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={ariaLabel}
      onMouseDown={onMouseDown}
      className="group relative -mx-[3px] h-full w-1.5 shrink-0 cursor-col-resize select-none"
    >
      <div className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-border transition-colors group-hover:bg-foreground/30 group-active:bg-foreground/60" />
    </div>
  );
}

// ─── Empty state ────────────────────────────────────────────

function EmptyState({ onTileClick }: { onTileClick: (tile: Tile) => void }) {
  return (
    <div className="mx-auto max-w-2xl py-16">
      <div className="mb-6 text-center">
        <div className="mb-3 inline-flex h-12 w-12 items-center justify-center rounded-full bg-muted">
          <Sparkles className="h-5 w-5 text-muted-foreground" />
        </div>
        <h2 className="mb-1 text-base font-medium">AI 交流</h2>
        <p className="text-xs text-muted-foreground">
          选一个快捷入口，或直接提问 / 拖入文件开始对话。
        </p>
      </div>
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
        {TILES.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => onTileClick(t)}
              className="flex items-start gap-2.5 rounded-lg border bg-background p-3 text-left transition-colors hover:border-foreground/40 hover:bg-muted"
            >
              <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0">
                <div className="text-sm font-medium">{t.label}</div>
                <div className="truncate text-[11px] text-muted-foreground">
                  {t.description}
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ─── ModelPicker ─────────────────────────────────────────────

function ModelPicker({
  available,
  current,
  onPick,
  disabled,
}: {
  available: V3ModelChoice[];
  current: string | null;
  onPick: (id: string | null) => void | Promise<void>;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  if (available.length === 0) return null;
  const activeId = current ?? available[0]?.id ?? null;
  const active = available.find((m) => m.id === activeId);

  return (
    <div className="relative" ref={ref}>
      <Button
        variant="ghost"
        size="sm"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className="h-8 px-2.5 text-xs gap-1.5"
      >
        <Cpu className="h-3.5 w-3.5" />
        {active?.label ?? "默认模型"}
      </Button>
      {open && (
        <div className="absolute right-0 top-full z-30 mt-1 w-72 rounded-lg border bg-popover p-1 shadow-md">
          {available.map((m) => {
            const selected = m.id === activeId;
            return (
              <button
                key={m.id}
                onClick={() => {
                  onPick(m.id);
                  setOpen(false);
                }}
                className={cn(
                  "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors",
                  selected ? "bg-accent" : "hover:bg-accent/60",
                )}
              >
                <div className="w-4 shrink-0 pt-0.5">
                  {selected && (
                    <Check className="h-3.5 w-3.5 text-foreground" />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{m.label}</div>
                  <div className="truncate text-[10px] text-muted-foreground">
                    {m.provider}
                    {m.notes ? ` · ${m.notes}` : ""}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
