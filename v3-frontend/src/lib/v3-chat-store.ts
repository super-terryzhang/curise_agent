/**
 * useV3Chat — React hook owning all v3 chat state.
 *
 * Responsibilities:
 * - Maintain { sessionId, messages, sending, pendingApprovals, error }
 * - sendMessage(text, file?): handles new vs existing session + auto-uploads
 *   files via /api/data-upload/upload, slipping the resulting batch_id
 *   into the prompt so the agent knows what to operate on.
 * - SSE stream subscription: applies incremental updates on streaming
 *   events (text_delta / tool_call_started / tool_args_delta /
 *   tool_result / assistant_message_done — ADR-0009), and reconciles
 *   with a final getSession() fetch on run_completed.
 * - HITL approvals: collect from approval_request events into a queue,
 *   resolve via decideAction.
 *
 * In-flight assistant message convention:
 *   We mark optimistically-built (streaming) messages with negative ids.
 *   When `assistant_message_done` arrives we "seal" them so the next
 *   text_delta starts a fresh in-flight message. On run_completed we
 *   refetch from the backend to replace the optimistic shapes with the
 *   canonical DB ones.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  cancelRun,
  createSession,
  decideAction as apiDecideAction,
  getSession,
  listModels,
  patchSession,
  sendMessage,
  streamSession,
  uploadDataFile,
  type V3ApprovalRequestEvent,
  type V3ArtifactEvent,
  type V3Message,
  type V3ModelChoice,
  type V3SessionDetail,
  type V3StreamEvent,
} from "./v3-chat-api";

export interface PendingApproval extends Omit<V3ApprovalRequestEvent, "type"> {
  status: "pending" | "approved" | "rejected" | "failed";
  result?: Record<string, unknown> | null;
}

// Tagged with a render-order id so the side-panel can show newest first
// without depending on timing (SSE events arrive in order but we want
// a stable React key).
export interface ChatArtifact extends Omit<V3ArtifactEvent, "type"> {
  id: number;
}

export interface ChatStoreState {
  sessionId: string | null;
  messages: V3Message[];
  sending: boolean;
  pendingApprovals: PendingApproval[];
  artifacts: ChatArtifact[];
  error: string | null;
  // Per-session model preference. `null` = use backend default (Gemini).
  // Only loaded once a session exists (otherwise null + draft mode);
  // setModel() flips it both locally and via PATCH so the *next* message
  // uses the chosen provider/model. Mid-flight runs keep their model.
  model: string | null;
  availableModels: V3ModelChoice[];
}

export interface ChatStore extends ChatStoreState {
  sendMessage: (
    text: string,
    file?: File | null,
    pageContext?: string | null,
  ) => Promise<void>;
  decideAction: (actionId: number, decision: "approve" | "reject") => Promise<void>;
  cancel: () => Promise<void>;
  resetSession: () => void;
  loadSession: (sessionId: string) => Promise<void>;
  setModel: (model: string | null) => Promise<void>;
}

export function useV3Chat(initialSessionId?: string | null): ChatStore {
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId ?? null);
  const [messages, setMessages] = useState<V3Message[]>([]);
  const [sending, setSending] = useState(false);
  const [pendingApprovals, setPendingApprovals] = useState<PendingApproval[]>([]);
  const [artifacts, setArtifacts] = useState<ChatArtifact[]>([]);
  const artifactSeqRef = useRef(0);
  const [error, setError] = useState<string | null>(null);
  const [model, setModelState] = useState<string | null>(null);
  const [availableModels, setAvailableModels] = useState<V3ModelChoice[]>([]);
  // Drafted model selection when no session exists yet — applied to
  // createSession() so the first turn already runs on the chosen model.
  const draftModelRef = useRef<string | null>(null);

  const streamAbortRef = useRef<(() => void) | null>(null);

  // Load model catalogue once. Failure is non-fatal — the UI just hides
  // the picker; sending still works (uses backend default).
  useEffect(() => {
    listModels()
      .then(setAvailableModels)
      .catch((e) => console.warn("[v3-chat-store] listModels failed:", e));
  }, []);

  // ─── Session loading & SSE subscription ───────────────

  const subscribe = useCallback((sid: string) => {
    if (streamAbortRef.current) {
      streamAbortRef.current();
      streamAbortRef.current = null;
    }
    streamAbortRef.current = streamSession(
      sid,
      (event: V3StreamEvent) => {
        switch (event.type) {
          case "run_started":
            setSending(true);
            break;
          case "run_completed":
            // Final fetch to reconcile our optimistic streaming with
            // the canonical persisted shape (e.g. real DB ids assigned).
            getSession(sid)
              .then((d: V3SessionDetail) => setMessages(d.messages))
              .catch((e) => setError(String(e)))
              .finally(() => setSending(false));
            break;
          case "run_error":
            setError(event.error);
            setSending(false);
            break;
          case "run_replaced":
          case "run_idle":
          case "ping":
            break;

          // ─── Streaming generation events (ADR-0009) ─────────
          case "text_delta":
            setMessages((prev) => appendToInflightAssistant(prev, sid, event.delta));
            break;
          case "reasoning_delta":
            setMessages((prev) =>
              appendToInflightAssistant(prev, sid, event.delta, /*isReasoning*/ true)
            );
            break;
          case "tool_call_started":
            setMessages((prev) =>
              addToolCallToInflight(prev, sid, event.call_id, event.tool_name)
            );
            break;
          case "tool_args_delta":
            setMessages((prev) =>
              appendToolArgs(prev, event.call_id, event.delta)
            );
            break;
          case "assistant_message_done":
            // Seal the in-flight assistant message; the next text/tool
            // event will start a fresh one (matches v3 backend's per-turn
            // assistant message persistence).
            setMessages((prev) => sealInflightAssistant(prev));
            break;
          case "tool_result":
            setMessages((prev) =>
              appendToolResult(prev, sid, event.tool_name, event.result)
            );
            break;
          case "skill_activated":
            // Arrives once at the start of a run when one or more
            // SKILL.md files match the user's task. Attach the list to
            // the inflight assistant message so MessageView can render
            // an "activated playbook" chip at the top of the bubble.
            setMessages((prev) =>
              attachSkillsToInflight(prev, sid, event.skills)
            );
            break;

          case "approval_request":
            setPendingApprovals((prev) =>
              prev.some((p) => p.action_id === event.action_id)
                ? prev
                : [
                    ...prev,
                    {
                      session_id: event.session_id,
                      action_id: event.action_id,
                      action: event.action,
                      target_kind: event.target_kind,
                      target_id: event.target_id,
                      payload: event.payload,
                      summary: event.summary,
                      status: "pending",
                    },
                  ]
            );
            break;
          case "approval_resolved":
            setPendingApprovals((prev) =>
              prev.map((p) =>
                p.action_id === event.action_id
                  ? { ...p, status: event.decision, result: event.result }
                  : p
              )
            );
            break;

          case "artifact":
            // Agent asked us to render a registered UI component (e.g.
            // upload_diff_viewer). The event carries only component name +
            // a small reference dict (e.g. {batch_id: 12}) + narration —
            // the component fetches the real data via the artifact
            // endpoint. This is the declarative-UI half of A2UI.
            artifactSeqRef.current += 1;
            setArtifacts((prev) => [
              ...prev,
              {
                id: artifactSeqRef.current,
                session_id: event.session_id,
                component: event.component,
                data: event.data,
                narration: event.narration,
                user_id: event.user_id,
              },
            ]);
            break;
        }
      },
      (err) => {
        console.warn("[v3-chat-store] SSE error:", err.message);
      }
    );
  }, []);

  const loadSession = useCallback(
    async (sid: string) => {
      setError(null);
      setSessionId(sid);
      try {
        const detail = await getSession(sid);
        setMessages(detail.messages);
        setPendingApprovals([]);
        // Rehydrate artifact panels from persisted history. Prior to v42
        // the backend didn't surface artifacts in this response and we
        // reset to []; that wiped every panel on refresh. The backend
        // now scans `present_artifact` tool_calls + their tool_results
        // and returns the successful ones — bump seqRef so SSE events
        // arriving later don't reuse an id we already assigned.
        const hydratedArtifacts = (detail.artifacts ?? []) as ChatArtifact[];
        setArtifacts(hydratedArtifacts);
        artifactSeqRef.current = hydratedArtifacts.length;
        setModelState(detail.model ?? null);
        subscribe(sid);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [subscribe]
  );

  useEffect(() => {
    if (initialSessionId) {
      loadSession(initialSessionId);
    }
    return () => {
      streamAbortRef.current?.();
      streamAbortRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSessionId]);

  // ─── Send message (with optional file upload) ─────────

  const handleSendMessage = useCallback(
    async (text: string, file?: File | null, pageContext?: string | null) => {
      setError(null);
      // Ensure we have a session
      let sid = sessionId;
      if (!sid) {
        try {
          const s = await createSession(
            text.slice(0, 40) || "新对话",
            draftModelRef.current ?? undefined
          );
          sid = s.id;
          setSessionId(sid);
          setModelState(s.model ?? null);
          draftModelRef.current = null;
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e));
          return;
        }
      }

      let finalText = text;

      if (file) {
        try {
          const uploadResult = await uploadDataFile(file);
          finalText =
            (text || "请帮我处理这份上传的数据") +
            `\n\n[已上传文件 ${file.name}，batch_id=${uploadResult.batch_id}, total_rows=${uploadResult.total_rows}]`;
        } catch (e) {
          setError(`文件上传失败: ${e instanceof Error ? e.message : String(e)}`);
          return;
        }
      }

      // Optimistic user message
      const optimistic: V3Message = {
        id: -Date.now(),
        session_id: sid,
        sequence: messages.length,
        role: "user",
        parts: [
          {
            type: "raw",
            schema: "openai-chat-completion",
            data: { role: "user", content: finalText },
          },
        ],
        model: null,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, optimistic]);
      setSending(true);

      try {
        // ⚠️ ORDER MATTERS:
        // The backend's GET /stream returns `run_idle` and closes
        // immediately if no chat_streams handle is registered for this
        // session. The handle is created INSIDE POST /messages (see
        // apps/http/chat.py:send_message → _chat_streams.register).
        //
        // So the correct order is:
        //   1. POST /messages → backend registers handle + starts bg job
        //   2. GET /stream → SSE attaches to the live handle's queue
        //
        // Doing them in reverse (subscribe first) raced against handle
        // creation: SSE saw no handle → run_idle → closed → events
        // never arrived → frontend stuck "thinking…"
        await sendMessage(sid, finalText, pageContext);
        subscribe(sid);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setSending(false);
      }
    },
    [sessionId, messages.length, subscribe]
  );

  // ─── Decide action ───────────────────────────────────

  const handleDecide = useCallback(
    async (actionId: number, decision: "approve" | "reject") => {
      try {
        const res = await apiDecideAction(actionId, decision);
        setPendingApprovals((prev) =>
          prev.map((p) =>
            p.action_id === actionId
              ? {
                  ...p,
                  status: (res.status as PendingApproval["status"]) ?? "approved",
                  result: res.result ?? null,
                }
              : p
          )
        );
        // After /decide returns, the backend has fired a follow-up agent
        // turn (see chat.py:_trigger_approval_followup): the dispatch
        // result is being fed back into a fresh agent run and the model
        // is generating a "✅ 已成功创建..." style message. Re-open SSE
        // so we receive its text_delta events live instead of waiting
        // for the next user message to refetch.
        if (sessionId) {
          subscribe(sessionId);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        throw e;
      }
    },
    [sessionId, subscribe]
  );

  // ─── Cancel + reset ──────────────────────────────────

  const handleCancel = useCallback(async () => {
    if (!sessionId) return;
    try {
      await cancelRun(sessionId);
    } catch {
      /* best effort */
    }
    setSending(false);
  }, [sessionId]);

  const resetSession = useCallback(() => {
    streamAbortRef.current?.();
    streamAbortRef.current = null;
    setSessionId(null);
    setMessages([]);
    setPendingApprovals([]);
    setArtifacts([]);
    artifactSeqRef.current = 0;
    setSending(false);
    setError(null);
    setModelState(null);
    // Carry the user's last pick into the next session — if they're
    // about to send another message they probably want the same model.
    // (resetSession is "new conversation", not "switch back to default".)
  }, []);

  const handleSetModel = useCallback(
    async (next: string | null) => {
      // No session yet: stash for createSession to consume.
      if (!sessionId) {
        draftModelRef.current = next;
        setModelState(next);
        return;
      }
      // Optimistic — UI flips immediately; if PATCH fails we surface the
      // error toast via setError but don't revert (the backend will fall
      // back to default which is the safe outcome).
      setModelState(next);
      try {
        await patchSession(sessionId, { model: next });
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [sessionId]
  );

  return {
    sessionId,
    messages,
    sending,
    pendingApprovals,
    artifacts,
    error,
    model,
    availableModels,
    sendMessage: handleSendMessage,
    decideAction: handleDecide,
    cancel: handleCancel,
    resetSession,
    loadSession,
    setModel: handleSetModel,
  };
}

// ─── Streaming helpers ────────────────────────────────────────
//
// In-flight messages have negative ids; they're replaced wholesale on
// `run_completed` when we refetch from the canonical backend store.
// The functions below mutate the messages array immutably (returning
// a new array) so React re-renders correctly.

function _isInflight(m: V3Message): boolean {
  return m.id < 0;
}

function _isAssistant(m: V3Message): boolean {
  const data = m.parts[0]?.data;
  return data && (data.role === "assistant" || m.role === "assistant");
}

function _isInflightAssistantOpen(m: V3Message): boolean {
  // "Open" = optimistic + assistant + not sealed yet (sealed marker is
  // metadata.sealed=true on the part)
  if (!_isInflight(m)) return false;
  if (!_isAssistant(m)) return false;
  // We stash a `_sealed` flag on the part during the stream; sealed
  // messages should not be appended to anymore.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const part: any = m.parts[0];
  return !part?._sealed;
}

function _newInflightAssistant(
  sessionId: string,
  sequence: number,
  initialContent = "",
  reasoning = ""
): V3Message {
  return {
    id: -Date.now() - Math.floor(Math.random() * 1000),
    session_id: sessionId,
    sequence,
    role: "assistant",
    parts: [
      {
        type: "raw",
        schema: "openai-chat-completion",
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        data: {
          role: "assistant",
          content: initialContent,
          tool_calls: [],
          ...(reasoning ? { reasoning_content: reasoning } : {}),
        } as any,
      },
    ],
    model: null,
    created_at: new Date().toISOString(),
  };
}

function appendToInflightAssistant(
  prev: V3Message[],
  sessionId: string,
  delta: string,
  isReasoning = false
): V3Message[] {
  // Find the last open assistant message; if none, create one
  let idx = -1;
  for (let i = prev.length - 1; i >= 0; i--) {
    if (_isInflightAssistantOpen(prev[i])) {
      idx = i;
      break;
    }
    // If we hit any other (non-inflight or sealed) message we still
    // want to start a fresh in-flight one rather than reach further
    // back. So break either way.
    break;
  }

  if (idx === -1) {
    return [
      ...prev,
      _newInflightAssistant(
        sessionId,
        prev.length,
        isReasoning ? "" : delta,
        isReasoning ? delta : ""
      ),
    ];
  }
  return prev.map((m, i) => {
    if (i !== idx) return m;
    const part = m.parts[0];
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const data: any = { ...(part.data as Record<string, unknown>) };
    if (isReasoning) {
      data.reasoning_content = (data.reasoning_content || "") + delta;
    } else {
      data.content = (data.content || "") + delta;
    }
    return {
      ...m,
      parts: [{ ...part, data }],
    };
  });
}

function attachSkillsToInflight(
  prev: V3Message[],
  sessionId: string,
  skills: { name: string; description: string }[]
): V3Message[] {
  // skill_activated arrives BEFORE any text_delta / tool_call_started,
  // so the inflight assistant doesn't exist yet. Same dance as the
  // other helpers: find-or-create and merge.
  let idx = -1;
  for (let i = prev.length - 1; i >= 0; i--) {
    if (_isInflightAssistantOpen(prev[i])) {
      idx = i;
      break;
    }
    break;
  }
  if (idx === -1) {
    const fresh = _newInflightAssistant(sessionId, prev.length, "");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (fresh.parts[0].data as any).skills_activated = skills;
    return [...prev, fresh];
  }
  return prev.map((m, i) => {
    if (i !== idx) return m;
    const part = m.parts[0];
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const data: any = { ...(part.data as Record<string, unknown>) };
    // Multiple skill_activated events on the same turn would be unusual
    // but if it happens, dedupe by name and keep the union.
    const existing = Array.isArray(data.skills_activated)
      ? (data.skills_activated as { name: string; description: string }[])
      : [];
    const seen = new Set(existing.map((s) => s.name));
    const merged = [...existing];
    for (const s of skills) {
      if (!seen.has(s.name)) {
        merged.push(s);
        seen.add(s.name);
      }
    }
    data.skills_activated = merged;
    return {
      ...m,
      parts: [{ ...part, data }],
    };
  });
}

function addToolCallToInflight(
  prev: V3Message[],
  sessionId: string,
  callId: string,
  toolName: string
): V3Message[] {
  // Find / create open assistant; append tool_call entry
  let idx = -1;
  for (let i = prev.length - 1; i >= 0; i--) {
    if (_isInflightAssistantOpen(prev[i])) {
      idx = i;
      break;
    }
    break;
  }
  if (idx === -1) {
    const fresh = _newInflightAssistant(sessionId, prev.length, "");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (fresh.parts[0].data as any).tool_calls = [
      { id: callId, type: "function", function: { name: toolName, arguments: "" } },
    ];
    return [...prev, fresh];
  }
  return prev.map((m, i) => {
    if (i !== idx) return m;
    const part = m.parts[0];
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const data: any = { ...(part.data as Record<string, unknown>) };
    const tcs = Array.isArray(data.tool_calls) ? [...data.tool_calls] : [];
    if (!tcs.find((t: { id: string }) => t.id === callId)) {
      tcs.push({
        id: callId,
        type: "function",
        function: { name: toolName, arguments: "" },
      });
    }
    data.tool_calls = tcs;
    return {
      ...m,
      parts: [{ ...part, data }],
    };
  });
}

function appendToolArgs(
  prev: V3Message[],
  callId: string,
  delta: string
): V3Message[] {
  return prev.map((m) => {
    if (!_isInflightAssistantOpen(m)) return m;
    const part = m.parts[0];
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const data: any = part.data as any;
    if (!Array.isArray(data.tool_calls)) return m;
    const tcs = data.tool_calls.map(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (tc: any) => {
        if (tc.id !== callId) return tc;
        return {
          ...tc,
          function: {
            ...tc.function,
            arguments: (tc.function?.arguments || "") + delta,
          },
        };
      }
    );
    return {
      ...m,
      parts: [{ ...part, data: { ...data, tool_calls: tcs } }],
    };
  });
}

function sealInflightAssistant(prev: V3Message[]): V3Message[] {
  // Mark the most recent in-flight assistant as sealed so the next
  // delta opens a fresh one.
  let sealed = false;
  return prev
    .slice()
    .reverse()
    .map((m) => {
      if (sealed || !_isInflightAssistantOpen(m)) return m;
      sealed = true;
      const part = m.parts[0];
      return {
        ...m,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        parts: [{ ...(part as any), _sealed: true }],
      };
    })
    .reverse();
}

function appendToolResult(
  prev: V3Message[],
  sessionId: string,
  toolName: string,
  result: string
): V3Message[] {
  // Append a synthetic optimistic tool message
  return [
    ...prev,
    {
      id: -Date.now() - Math.floor(Math.random() * 1000) - 1,
      session_id: sessionId,
      sequence: prev.length,
      role: "tool",
      parts: [
        {
          type: "raw",
          schema: "openai-chat-completion",
          data: {
            role: "tool",
            content: result,
            name: toolName,
          },
        },
      ],
      model: null,
      created_at: new Date().toISOString(),
    },
  ];
}
