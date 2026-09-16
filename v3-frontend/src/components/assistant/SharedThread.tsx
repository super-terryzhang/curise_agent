"use client";

/**
 * SharedThread — the single Thread render reused by both the full-page
 * /dashboard/agent and the floating AssistantSidebar.
 *
 * Why share, not duplicate:
 *   The Thread render encodes 13 typed tool UIs, HITL approval cards,
 *   reasoning blocks, markdown rendering, copy-to-composer affordance,
 *   skill chips, and the composer (with cancel-mid-stream gating).
 *   Maintaining two copies would diverge the moment one site adds a
 *   feature — the user would see different chat UX depending on
 *   which surface they opened. Single source of truth eliminates that
 *   class of bug at the architectural level.
 *
 * What lives here:
 *   - The `ThreadPrimitive.Root` shell + viewport + messages + scroll
 *     button + composer bar.
 *   - The per-message renderers (UserMessage, AssistantMessage) and
 *     part renderers (Text, Reasoning, Skills, Approval).
 *   - The composer with attachment chip + file picker (the picker
 *     button itself is hideable via `composer="text-only"` so the
 *     sidebar can opt out of file uploads in v1).
 *
 * What does NOT live here (stays in the caller):
 *   - Header chrome (model picker, panel toggles) — those are
 *     site-specific to the agent page or to the sidebar.
 *   - Drag-drop overlay — that's a page-level affordance.
 *   - Side columns (SessionList, ArtifactPane) — also page-specific.
 *
 * The `emptyState` prop lets each caller customize what the user
 * sees before the first message: the agent page shows quick-start
 * tiles, the sidebar can show a simpler "Ask me anything" pitch.
 */

import { useRef, useState, type ReactNode } from "react";
import {
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAui,
  type DataMessagePartComponent,
  type ReasoningMessagePartComponent,
} from "@assistant-ui/react";
import {
  ArrowDown,
  ArrowUp,
  Copy,
  Paperclip,
  Sparkles,
  Square,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { MarkdownContent } from "@/components/markdown-content";
import { type ApprovalDataPart } from "@/lib/assistant-ui-adapter";
import { useAssistantChat } from "./AssistantProvider";
import { buildToolUIs, FallbackToolUI } from "@/app/dashboard/agent/tool-uis";

const ALLOWED_EXTS = [".xlsx", ".xls", ".pdf", ".csv"];
const MAX_FILE_MB = 20;

function validateFile(f: File): string | null {
  const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
  if (!ALLOWED_EXTS.includes(ext)) {
    return `不支持的文件类型 (${ext}). 支持: ${ALLOWED_EXTS.join(", ")}`;
  }
  if (f.size > MAX_FILE_MB * 1024 * 1024) {
    return `文件过大 (>${MAX_FILE_MB}MB)`;
  }
  return null;
}

// Tool-call registry — assembled once per module load. Constant across
// renders so React doesn't churn child identities every turn. Both the
// agent page and the sidebar resolve through this same singleton, so
// adding a new tool UI propagates everywhere with zero per-site work.
const TOOL_UIS = buildToolUIs();

export interface SharedThreadProps {
  /** Rendered when the thread has no messages yet — the agent page
   *  passes its quick-start tiles; the sidebar can pass a simpler
   *  prompt or just `null`. */
  emptyState?: ReactNode;
  /** When true the composer hides its attach-file button. The
   *  sidebar uses this in v1 (the file flow needs the broader
   *  drag-drop target which only the agent page provides). Default
   *  false (full composer). */
  textOnlyComposer?: boolean;
  /** Lets the caller annotate the outer container with custom
   *  classes (e.g. the sidebar narrows the max-width). */
  className?: string;
}

export function SharedThread({
  emptyState,
  textOnlyComposer = false,
  className = "",
}: SharedThreadProps) {
  const chat = useAssistantChat();

  // Approval renderer needs `chat.decideAction`; we recreate it
  // per render but its identity stability is irrelevant — it's
  // passed as a prop to AssistantMessage which is itself a per-render
  // closure.
  const ApprovalRenderer: DataMessagePartComponent<ApprovalDataPart> = ({
    data,
  }) => <ApprovalView data={data} onDecide={chat.decideAction} />;

  return (
    <ThreadPrimitive.Root
      className={`relative flex flex-1 flex-col overflow-hidden ${className}`}
    >
      <ThreadPrimitive.Viewport className="flex-1 overflow-y-auto px-4 py-6">
        <div className="mx-auto max-w-3xl space-y-4">
          {emptyState && (
            <ThreadPrimitive.Empty>{emptyState}</ThreadPrimitive.Empty>
          )}

          <ThreadPrimitive.Messages
            components={{
              UserMessage,
              AssistantMessage: () => (
                <AssistantMessage Approval={ApprovalRenderer} />
              ),
            }}
          />

          {chat.error && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              {chat.error}
            </div>
          )}
        </div>
      </ThreadPrimitive.Viewport>

      <ThreadPrimitive.ScrollToBottom asChild>
        <button
          className="absolute bottom-24 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1 rounded-full border bg-background/95 px-3 py-1 text-xs shadow-sm backdrop-blur hover:bg-muted"
          aria-label="滚到底部"
        >
          <ArrowDown className="h-3 w-3" />
          最新消息
        </button>
      </ThreadPrimitive.ScrollToBottom>

      <ComposerBar textOnly={textOnlyComposer} isRunning={chat.sending} />
    </ThreadPrimitive.Root>
  );
}

// ─── Composer ─────────────────────────────────────────────────

function ComposerBar({
  textOnly,
  isRunning,
}: {
  textOnly: boolean;
  isRunning: boolean;
}) {
  const { attachedFile, setAttachedFile } = useAssistantChat();
  const fileInputRef = useRef<HTMLInputElement>(null);

  return (
    <div className="shrink-0 border-t bg-background/95 px-4 py-3 backdrop-blur">
      <div className="mx-auto max-w-3xl">
        {attachedFile && !textOnly && (
          <div className="mb-2 flex items-center gap-2 rounded-md border border-border bg-muted/60 px-2.5 py-1.5 text-xs">
            <Paperclip className="h-3 w-3 text-muted-foreground" />
            <span className="truncate">{attachedFile.name}</span>
            <span className="ml-auto text-muted-foreground">
              {(attachedFile.size / 1024).toFixed(1)} KB
            </span>
            <button
              type="button"
              onClick={() => setAttachedFile(null)}
              className="text-muted-foreground hover:text-foreground"
              aria-label="移除附件"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        )}

        <ComposerPrimitive.Root className="flex items-end gap-2 rounded-xl border bg-background p-1.5 shadow-sm focus-within:ring-2 focus-within:ring-ring/20">
          {!textOnly && (
            <>
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={isRunning}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted disabled:opacity-50"
                aria-label="添加附件"
              >
                <Paperclip className="h-4 w-4" />
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept={ALLOWED_EXTS.join(",")}
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (!f) return;
                  const err = validateFile(f);
                  if (err) {
                    toast.error(err);
                    e.target.value = "";
                    return;
                  }
                  setAttachedFile(f);
                  e.target.value = "";
                }}
              />
            </>
          )}
          <ComposerPrimitive.Input
            placeholder="输入消息... (Enter 发送，Shift+Enter 换行)"
            rows={1}
            className="max-h-48 min-h-[24px] flex-1 resize-none bg-transparent px-2 py-1.5 text-sm focus:outline-none"
          />
          <ThreadPrimitive.If running={false}>
            <ComposerPrimitive.Send
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-foreground text-background hover:bg-foreground/85 disabled:opacity-50"
              aria-label="发送"
            >
              <ArrowUp className="h-4 w-4" />
            </ComposerPrimitive.Send>
          </ThreadPrimitive.If>
          <ThreadPrimitive.If running>
            <ComposerPrimitive.Cancel
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border bg-background hover:bg-muted"
              aria-label="停止生成"
            >
              <Square className="h-3 w-3 fill-foreground text-foreground" />
            </ComposerPrimitive.Cancel>
          </ThreadPrimitive.If>
        </ComposerPrimitive.Root>
      </div>
    </div>
  );
}

// ─── Message renderers ──────────────────────────────────────

function UserMessage() {
  const aui = useAui();
  const copyToComposer = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation();
    const root =
      (e.currentTarget.closest("[data-um-root]") as HTMLElement) || null;
    const bubble = root?.querySelector("[data-um-text]") as HTMLElement | null;
    const text = bubble?.innerText?.trim() ?? "";
    if (!text) return;
    aui.composer().setText(text);
    setTimeout(() => {
      document.querySelector<HTMLTextAreaElement>("textarea")?.focus();
    }, 0);
  };
  return (
    <MessagePrimitive.Root className="group flex justify-end" data-um-root>
      <button
        type="button"
        onClick={copyToComposer}
        title="复制到输入框"
        aria-label="复制到输入框"
        className="mr-1 mt-1 self-start rounded-md p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-muted hover:text-foreground group-hover:opacity-100"
      >
        <Copy className="h-3 w-3" />
      </button>
      <div
        className="max-w-[80%] rounded-2xl bg-foreground px-4 py-2 text-sm text-background"
        data-um-text
      >
        <MessagePrimitive.Parts />
      </div>
    </MessagePrimitive.Root>
  );
}

function AssistantMessage({
  Approval,
}: {
  Approval: DataMessagePartComponent<ApprovalDataPart>;
}) {
  return (
    <MessagePrimitive.Root className="flex justify-start">
      <div className="max-w-[80%] space-y-2">
        <MessagePrimitive.Parts
          components={{
            Text: TextPart,
            Reasoning: ReasoningPart,
            data: {
              by_name: {
                approval: Approval,
                skills: SkillsPart as DataMessagePartComponent<unknown>,
              },
            },
            tools: { by_name: TOOL_UIS, Fallback: FallbackToolUI },
          }}
        />
      </div>
    </MessagePrimitive.Root>
  );
}

function TextPart(props: { text?: string }) {
  const text = props.text || "";
  if (!text) return null;
  return (
    <div className="rounded-2xl bg-muted px-4 py-2.5 text-sm leading-relaxed">
      <MarkdownContent content={text} />
    </div>
  );
}

const ReasoningPart: ReasoningMessagePartComponent = ({ text }) => {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return (
    <details
      open={open}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
      className="rounded-md border border-dashed border-muted-foreground/30 bg-muted/30 px-3 py-1.5 text-xs"
    >
      <summary className="cursor-pointer select-none text-muted-foreground hover:text-foreground">
        {open ? "▾" : "▸"} 思考过程 ({text.length} 字符)
      </summary>
      <div className="mt-2 whitespace-pre-wrap text-muted-foreground">
        {text}
      </div>
    </details>
  );
};

function SkillsPart({
  data,
}: {
  data?: { skills?: Array<{ name: string; description: string }> };
}) {
  const skills = data?.skills || [];
  if (skills.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {skills.map((s) => (
        <span
          key={s.name}
          title={s.description}
          className="inline-flex items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 text-[10px] font-medium text-foreground"
        >
          <Sparkles className="h-2.5 w-2.5" />
          {s.name}
        </span>
      ))}
    </div>
  );
}

// ─── Approval card ─────────────────────────────────────────

function ApprovalView({
  data,
  onDecide,
}: {
  data: ApprovalDataPart;
  onDecide: (id: number, decision: "approve" | "reject") => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const decide = async (decision: "approve" | "reject") => {
    setBusy(true);
    try {
      await onDecide(data.action_id, decision);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };

  if (data.status !== "pending") {
    const label =
      data.status === "approved"
        ? "已确认"
        : data.status === "rejected"
          ? "已取消"
          : "失败";
    return (
      <div className="rounded-md border border-muted bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
        {label} · #{data.action_id}
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-border bg-background p-4 shadow-sm">
      <div className="mb-2 flex items-center gap-2">
        <span className="rounded-full bg-foreground px-1.5 py-0.5 text-[10px] font-semibold text-background">
          需要确认
        </span>
        <code className="text-[11px] text-muted-foreground">{data.action}</code>
        <span className="ml-auto text-[10px] text-muted-foreground">
          #{data.action_id}
        </span>
      </div>
      <p className="mb-3 text-sm text-foreground">{data.summary}</p>
      <details className="mb-3">
        <summary className="cursor-pointer text-[10px] text-muted-foreground hover:text-foreground">
          ▸ payload
        </summary>
        <pre className="mt-1 max-h-32 overflow-auto rounded bg-muted px-2 py-1 text-[11px]">
          {JSON.stringify(data.payload, null, 2)}
        </pre>
      </details>
      <div className="flex gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => decide("reject")}
          className="rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium hover:bg-muted disabled:opacity-50"
        >
          取消
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => decide("approve")}
          className="rounded-md bg-foreground px-3 py-1.5 text-xs font-medium text-background hover:bg-foreground/85 disabled:opacity-50"
        >
          {busy ? "处理中..." : "确认执行"}
        </button>
      </div>
    </div>
  );
}
