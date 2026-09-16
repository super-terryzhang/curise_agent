"use client";

/**
 * AssistantProvider — single hoist point for the AI chat runtime + state.
 *
 * Why hoist:
 *   `useV3Chat` is per-instance — each call creates its own SSE
 *   subscription, message buffer, and pendingApprovals queue. If two
 *   components in the same tree both call it, the user gets two
 *   independent conversations talking to the same backend session,
 *   which is the classic race condition (one component's SSE event
 *   never reaches the other's render path).
 *
 *   The pattern we now want is: ONE `useV3Chat` instance lives at
 *   `dashboard/layout.tsx`, and both `/dashboard/agent`'s full-page
 *   thread AND the new floating sidebar consume the SAME chat object
 *   via React context. This is the single source of truth.
 *
 * What the provider owns:
 *   - the `useV3Chat` instance (sessionId, messages, pendingApprovals,
 *     artifacts, model, etc.)
 *   - the assistant-ui runtime built on top (memoized — the original
 *     site in agent/page.tsx rebuilt this on every render which churned
 *     the entire Thread tree)
 *   - the `attachedFile` slot (Composer state shared between the
 *     agent-page composer and the sidebar composer — sidebar may not
 *     show an attach button in v1 but the slot is shared so future
 *     symmetry is free)
 *   - `<AssistantRuntimeProvider>` (so all consumers see the same
 *     runtime) + `<ArtifactSelectorProvider>` (so artifact selection
 *     from inline chips works regardless of which thread mounted them)
 *
 * What the provider does NOT own:
 *   - URL ↔ session sync — that's an agent-page concern (deep links
 *     like `/dashboard/agent?session=abc` are agent-page-only;
 *     navigating to other pages must not clobber the URL with a
 *     session param). The page calls `chat.loadSession(id)` in its
 *     own `useEffect`.
 *   - Visual chrome (sidebar collapse, model picker placement,
 *     drag-drop overlay) — each consumer renders these for itself.
 *
 * Error boundary:
 *   `useAssistantChat()` throws if called outside the provider.
 *   That's a programmer error, not a runtime concern — let it crash
 *   loudly in dev so the missing-provider site is obvious.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  type AppendMessage,
} from "@assistant-ui/react";
import { toast } from "sonner";

import { ArtifactSelectorProvider } from "./ArtifactSelectorContext";
import { buildAssistantMessages } from "@/lib/assistant-ui-adapter";
import { useV3Chat, type ChatStore } from "@/lib/v3-chat-store";
import { usePageContext } from "./usePageContext";

/** Public shape of what `useAssistantChat()` returns. We re-export
 *  `ChatStore` so callers don't have to chase the import path. */
export type AssistantChat = ChatStore & {
  /** File currently staged for the next outgoing message, or null.
   *  Lives at provider level so the agent-page composer can set it
   *  via drag-drop and the sidebar composer (which doesn't show the
   *  attach UI) implicitly inherits "no file" without prop drilling. */
  attachedFile: File | null;
  setAttachedFile: (f: File | null) => void;
};

const ChatCtx = createContext<AssistantChat | null>(null);

export function useAssistantChat(): AssistantChat {
  const ctx = useContext(ChatCtx);
  if (!ctx) {
    // Loud failure intentional — see file header. A silent null would
    // let a buggy mount path render an empty Thread that silently
    // drops every message.
    throw new Error(
      "useAssistantChat must be called within an <AssistantProvider>. " +
        "Make sure your component tree is wrapped at dashboard/layout.tsx.",
    );
  }
  return ctx;
}

export interface AssistantProviderProps {
  children: ReactNode;
  /** Initial session id to bootstrap with. The agent page reads
   *  `?session=<id>` from the URL and passes it here for deep-link
   *  support; non-agent pages pass `null` and inherit whatever
   *  session the user had open most recently. */
  initialSessionId?: string | null;
}

export function AssistantProvider({
  children,
  initialSessionId = null,
}: AssistantProviderProps) {
  const chat = useV3Chat(initialSessionId);
  const [attachedFile, setAttachedFile] = useState<File | null>(null);

  // Page context = the URL the user is on RIGHT NOW. Captured as a
  // ref so the onNew closure below doesn't re-create when only the
  // pathname changes (which it does on every nav). Read at send-time,
  // not declared as a dep — that way the closure stays stable but
  // each send picks up the latest URL.
  const pageContext = usePageContext();
  const pageContextRef = useRef(pageContext);
  pageContextRef.current = pageContext;

  // Merge useV3Chat's pendingApprovals into the messages timeline
  // so assistant-ui's renderer treats approval cards as a normal
  // `data` part on the latest assistant message. See
  // `lib/assistant-ui-adapter.ts` for the projection rules.
  const messages = useMemo(
    () => buildAssistantMessages(chat.messages, chat.pendingApprovals),
    [chat.messages, chat.pendingApprovals],
  );

  // Stable callbacks so the runtime config below doesn't churn on
  // every render. Each closes over the latest `chat` ref by being
  // recreated when chat.sendMessage / chat.cancel identity changes
  // (those are stable across renders inside useV3Chat).
  const onNew = useCallback(
    async (message: AppendMessage) => {
      const text = message.content
        .filter((p) => p.type === "text")
        .map((p) => (p as { type: "text"; text: string }).text)
        .join("")
        .trim();
      if (!text && !attachedFile) return;
      const f = attachedFile;
      setAttachedFile(null);
      try {
        await chat.sendMessage(text, f, pageContextRef.current);
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "发送失败");
      }
    },
    [attachedFile, chat],
  );

  const onCancel = useCallback(async () => {
    try {
      await chat.cancel();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "取消失败");
    }
  }, [chat]);

  // Imperative-send bridge: any in-page button can fire
  // `window.dispatchEvent(new CustomEvent("assistant:openAndSend", {detail:{text}}))`
  // and the message lands on the same session the sidebar shows.
  // The sidebar listens for the same event to pop itself open.
  // Why a DOM event rather than context: callers (e.g. FinancialTab)
  // are deep in the tree and don't otherwise need provider context.
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ text?: string }>).detail || {};
      const text = (detail.text || "").trim();
      if (!text) return;
      chat.sendMessage(text, null, pageContextRef.current).catch((err) =>
        toast.error(err instanceof Error ? err.message : "发送失败"),
      );
    };
    window.addEventListener("assistant:openAndSend", handler);
    return () => window.removeEventListener("assistant:openAndSend", handler);
  }, [chat]);

  const runtime = useExternalStoreRuntime({
    messages,
    isRunning: chat.sending,
    convertMessage: (m) => m,
    // useV3Chat owns the message buffer; setMessages is a no-op
    // because assistant-ui's MessageRepository can only ever
    // mirror our state, never mutate it back.
    setMessages: () => {},
    onNew,
    onCancel,
  });

  const chatValue = useMemo<AssistantChat>(
    () => ({ ...chat, attachedFile, setAttachedFile }),
    [chat, attachedFile],
  );

  return (
    <ChatCtx.Provider value={chatValue}>
      <ArtifactSelectorProvider artifacts={chat.artifacts}>
        <AssistantRuntimeProvider runtime={runtime}>
          {children}
        </AssistantRuntimeProvider>
      </ArtifactSelectorProvider>
    </ChatCtx.Provider>
  );
}
