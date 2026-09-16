/**
 * useV3Chat → assistant-ui adapter.
 *
 * Goal: keep our `useV3Chat` hook as the single source of truth for chat
 * state (it owns SSE handling, token refresh, model picker, HITL approval
 * queue, artifact panel) and expose those messages to assistant-ui's
 * `useExternalStoreRuntime` as the renderer.
 *
 * Why an adapter at all? Our `V3Message.parts[]` is shaped after the
 * OpenAI chat-completion schema (`role`, `content`, `tool_calls`), but
 * assistant-ui consumes `ThreadMessageLike` parts (typed parts: text,
 * tool-call, reasoning, data). The adapter is a pure function that
 * translates each V3Message into one ThreadMessageLike — testable,
 * deterministic, no React state.
 *
 * HITL approvals: when the agent emits an `approval_request` SSE event,
 * `useV3Chat` queues it into `pendingApprovals[]`. We project the
 * pending list into the LAST assistant message as a `{ type: "data",
 * data: { kind: "approval", ... } }` part, and render that via the
 * assistant-ui `DataRenderers` registry. Approve/reject buttons call
 * back into `decideAction(actionId, decision)`.
 */

import type { ThreadMessageLike } from "@assistant-ui/react";
import type { PendingApproval } from "@/lib/v3-chat-store";
import type { V3Message } from "@/lib/v3-chat-api";

/**
 * `data-approval` part payload — what we attach to messages so the
 * assistant-ui DataRenderers can find and render the approval card.
 *
 * Public for downstream tests and the renderer component to share the
 * same shape.
 */
export interface ApprovalDataPart {
  kind: "approval";
  action_id: number;
  action: string;
  target_kind: string;
  target_id: number | null;
  payload: Record<string, unknown>;
  summary: string;
  status: "pending" | "approved" | "rejected" | "failed";
}

interface V3RawPartData {
  role: string;
  content?: string | null;
  /**
   * `reasoning_content` is our schema's non-standard field for the
   * model's chain-of-thought stream (emitted on `reasoning_delta` SSE
   * events). The OpenAI chat-completion spec doesn't include it, but
   * useV3Chat persists it alongside content for replay. We surface it
   * as a `reasoning` part so it renders in a collapsible block.
   */
  reasoning_content?: string;
  tool_calls?: Array<{
    id: string;
    type: "function";
    function: { name: string; arguments: string };
  }>;
  tool_call_id?: string;
  name?: string;
  /**
   * Skills activated for this assistant turn — populated by the
   * `skill_activated` SSE event. We surface as a `data-skills` part so
   * the renderer can show an "activated playbook" chip.
   */
  skills_activated?: Array<{ name: string; description: string }>;
}

/**
 * Convert one V3Message into one assistant-ui ThreadMessageLike.
 *
 * Mapping rules (each verified against `v3-chat-api.ts:52`):
 *   - user message with content → { role: "user", parts: [{text}] }
 *   - assistant text → { role: "assistant", parts: [{text}] }
 *   - assistant tool_calls → tool-call parts (one per call)
 *   - tool role → represented as a tool-call RESULT (assistant-ui's
 *     `tool-call` part carries `result` post-completion)
 *
 * Returns `null` for system messages — the chat UI doesn't render those.
 */
export function v3MessageToAssistantMessage(
  msg: V3Message,
): ThreadMessageLike | null {
  if (msg.role === "system") return null;

  const firstRaw = msg.parts.find((p) => p.type === "raw");
  const data = (firstRaw?.data || {}) as V3RawPartData;

  // ── User ─────────────────────────────────────────────────
  if (msg.role === "user") {
    const text = typeof data.content === "string" ? data.content : "";
    return {
      id: String(msg.id),
      role: "user",
      content: text ? [{ type: "text", text }] : [],
    };
  }

  // ── Tool result (role="tool") ───────────────────────────
  // assistant-ui represents tool results inside the assistant's
  // preceding tool-call part. Since we receive them as separate
  // messages from the API, we surface as a stand-alone assistant
  // message with the result text — keeps PoC simple. Real merge logic
  // lives in `mergeToolResultsIntoAssistant` (Phase 1).
  if (msg.role === "tool") {
    return {
      id: String(msg.id),
      role: "assistant",
      content: [
        {
          type: "tool-call",
          toolCallId: data.tool_call_id || `tool-${msg.id}`,
          toolName: data.name || "tool",
          args: {},
          result: typeof data.content === "string" ? data.content : "",
        },
      ],
    };
  }

  // ── Assistant ────────────────────────────────────────────
  // Build content array as ThreadMessageLike's accepted shape. Note:
  // assistant-ui uses ReadonlyJSON for tool args, so we let TS infer the
  // narrow shape and only assert at the boundary (it's structurally JSON
  // by construction — JSON.parse returns plain values; the _raw fallback
  // is a string).
  type Part =
    | { type: "text"; text: string }
    | { type: "reasoning"; text: string }
    | {
        type: "data";
        name: "skills";
        data: { skills: Array<{ name: string; description: string }> };
      }
    | {
        type: "tool-call";
        toolCallId: string;
        toolName: string;
        args: unknown;
      };
  const parts: Part[] = [];

  // Skills come BEFORE everything else — they're the "activated playbook"
  // banner that the agent emits the moment it picks a skill.
  if (data.skills_activated && data.skills_activated.length > 0) {
    parts.push({
      type: "data",
      name: "skills",
      data: { skills: data.skills_activated },
    });
  }

  // Reasoning is emitted as a deltas stream and lives in our schema's
  // non-standard `reasoning_content` field. Render BEFORE final text so
  // the chain-of-thought block sits above the conclusion.
  if (
    typeof data.reasoning_content === "string" &&
    data.reasoning_content.trim().length > 0
  ) {
    parts.push({ type: "reasoning", text: data.reasoning_content });
  }

  if (typeof data.content === "string" && data.content) {
    parts.push({ type: "text", text: data.content });
  }
  for (const tc of data.tool_calls || []) {
    let args: unknown = {};
    try {
      args = JSON.parse(tc.function.arguments || "{}");
    } catch {
      // Stream-in-progress args won't be valid JSON yet; fall back to
      // raw text so the UI shows *something* during streaming.
      args = { _raw: tc.function.arguments };
    }
    parts.push({
      type: "tool-call",
      toolCallId: tc.id,
      toolName: tc.function.name,
      args,
    });
  }
  return {
    id: String(msg.id),
    role: "assistant",
    content: (parts.length > 0
      ? parts
      : [{ type: "text", text: "" }]) as ThreadMessageLike["content"],
  };
}

/**
 * Project the live `pendingApprovals` list into the LAST assistant
 * message as `data-approval` parts. The agent's narration came before
 * the approval was emitted, so visually anchoring the card to the same
 * message keeps the conversation flow tight.
 *
 * Pure function: takes a list of converted messages + the current
 * approval queue, returns a new list with approval parts spliced in.
 */
export function attachApprovalsToLastAssistant(
  messages: ThreadMessageLike[],
  approvals: PendingApproval[],
): ThreadMessageLike[] {
  if (approvals.length === 0) return messages;

  // Find the last assistant message; if none, append a synthetic one
  // so the approval card has somewhere to live.
  const lastAssistantIdx = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "assistant") return i;
    }
    return -1;
  })();

  const approvalParts = approvals.map(
    (a): { type: "data"; name: "approval"; data: ApprovalDataPart } => ({
      type: "data",
      name: "approval", // matches the DataRenderers key in <Thread>
      data: {
        kind: "approval",
        action_id: a.action_id,
        action: a.action,
        target_kind: a.target_kind,
        target_id: a.target_id,
        payload: a.payload,
        summary: a.summary,
        status: a.status,
      },
    }),
  );

  if (lastAssistantIdx === -1) {
    return [
      ...messages,
      {
        id: `pending-approvals-${approvals[0].action_id}`,
        role: "assistant",
        content: approvalParts,
      },
    ];
  }

  const out = [...messages];
  const target = out[lastAssistantIdx];
  out[lastAssistantIdx] = {
    ...target,
    content: [
      ...(Array.isArray(target.content) ? target.content : []),
      ...approvalParts,
    ],
  } as ThreadMessageLike;
  return out;
}

/**
 * Convenience: full pipeline V3 messages + approvals → assistant-ui
 * ThreadMessageLike[] ready for `useExternalStoreRuntime({ messages, ... })`.
 */
export function buildAssistantMessages(
  v3Messages: V3Message[],
  approvals: PendingApproval[],
): ThreadMessageLike[] {
  const converted: ThreadMessageLike[] = [];
  for (const m of v3Messages) {
    const c = v3MessageToAssistantMessage(m);
    if (c) converted.push(c);
  }
  return attachApprovalsToLastAssistant(converted, approvals);
}
