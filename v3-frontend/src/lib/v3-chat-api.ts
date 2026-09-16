/**
 * v3 backend chat API — minimal client speaking the v3 protocol.
 *
 * v3 Endpoints (different from v2!):
 *   POST   /api/chat/sessions                  → create
 *   GET    /api/chat/sessions                  → list
 *   GET    /api/chat/sessions/{id}             → fetch session + messages
 *   DELETE /api/chat/sessions/{id}
 *   POST   /api/chat/sessions/{id}/messages    JSON {text}
 *   GET    /api/chat/sessions/{id}/stream      SSE
 *   POST   /api/chat/sessions/{id}/cancel
 *   POST   /api/chat/actions/{id}/decide       JSON {decision: approve|reject}
 *   POST   /api/data-upload/upload             FormData (file)
 *
 * v3 SSE event types (after ADR-0009 streaming model):
 *   run_started | run_completed | run_error | run_replaced
 *   text_delta | reasoning_delta
 *   tool_call_started | tool_args_delta | tool_result
 *   assistant_message_done
 *   approval_request | approval_resolved
 *   artifact        — declarative-UI hand-off; agent asks the frontend
 *                     to render a registered component (e.g.
 *                     upload_diff_viewer) with a reference payload.
 *                     The frontend fetches the actual data via
 *                     /api/artifacts/<kind>/<ref>. See
 *                     docs/current_progress/2026-05-19/agent-html-interaction-architecture.md
 *   ping | run_idle
 */

import { fetchWithAuth } from "./fetch-with-auth";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

// ─── Types ─────────────────────────────────────────────────

export interface V3Session {
  id: string;
  title: string;
  status: string;
  model?: string | null;
  created_at: string | null;
  updated_at?: string | null;
}

export interface V3ModelChoice {
  id: string;
  label: string;
  provider: string;
  notes?: string;
}

export interface V3Message {
  id: number;
  session_id: string;
  sequence: number;
  role: "user" | "assistant" | "tool" | "system";
  parts: Array<{
    type: "raw";
    schema: "openai-chat-completion";
    data: {
      role: string;
      content?: string | null;
      tool_calls?: Array<{
        id: string;
        type: "function";
        function: { name: string; arguments: string };
      }>;
      tool_call_id?: string;
      name?: string;
      // Skills activated for this assistant turn — populated by the
      // `skill_activated` SSE event before any text/tool deltas. Used by
      // MessageView's <SkillChip> to render an "activated playbook"
      // marker at the top of the message.
      skills_activated?: { name: string; description: string }[];
    };
  }>;
  model?: string | null;
  created_at: string | null;
}

export interface V3PersistedArtifact {
  id: number;
  session_id: string;
  component: string;
  data: Record<string, unknown>;
  narration: string;
  user_id: number;
}

export interface V3SessionDetail extends V3Session {
  messages: V3Message[];
  /**
   * Artifacts reconstructed from persisted `present_artifact` tool_calls
   * in this session's message history. Populated by GET /sessions/{id};
   * the v3-chat-store hydrates these into the live `artifacts` state on
   * page refresh so panels survive reload (prod 2026-05-20 fix).
   *
   * Optional for forward/backward compat with older backends.
   */
  artifacts?: V3PersistedArtifact[];
}

export interface V3ApprovalRequestEvent {
  type: "approval_request";
  session_id: string;
  action_id: number;
  action: string;
  target_kind: string;
  target_id: number | null;
  payload: Record<string, unknown>;
  summary: string;
}

export interface V3ApprovalResolvedEvent {
  type: "approval_resolved";
  session_id: string;
  action_id: number;
  decision: "approved" | "rejected" | "failed";
  result: Record<string, unknown>;
}

// Declarative generative-UI handoff. Agent picks a component from the
// backend's catalog (agent/runtime/artifacts.py) and the frontend renders
// the matching React component, fetching data via REST. See
// docs/current_progress/2026-05-19/agent-html-interaction-architecture.md
// for the architecture rationale + measurements.
export interface V3ArtifactEvent {
  type: "artifact";
  session_id: string;
  component: string;
  data: Record<string, unknown>;
  narration: string;
  user_id?: number;
}

// Streaming events (ADR-0009)
export interface V3TextDeltaEvent {
  type: "text_delta";
  session_id: string;
  delta: string;
}
export interface V3ReasoningDeltaEvent {
  type: "reasoning_delta";
  session_id: string;
  delta: string;
}
export interface V3ToolCallStartedEvent {
  type: "tool_call_started";
  session_id: string;
  call_id: string;
  tool_name: string;
}
export interface V3ToolArgsDeltaEvent {
  type: "tool_args_delta";
  session_id: string;
  call_id: string;
  delta: string;
}
export interface V3ToolResultEvent {
  type: "tool_result";
  session_id: string;
  tool_name: string;
  args: Record<string, unknown>;
  result: string;
}
export interface V3AssistantMessageDoneEvent {
  type: "assistant_message_done";
  session_id: string;
}

// Skill activation — fired once per turn when one or more SKILL.md files
// match the user's message. Powers the "activated skill" chip rendered
// in the assistant message header (cf. MessageView.tsx → SkillChip).
export interface V3SkillActivatedEvent {
  type: "skill_activated";
  session_id: string;
  skills: { name: string; description: string }[];
}

export type V3StreamEvent =
  | { type: "run_started"; session_id: string }
  | { type: "run_completed"; session_id: string }
  | { type: "run_error"; session_id: string; error: string }
  | { type: "run_replaced"; session_id: string }
  | { type: "run_idle"; session_id: string }
  | { type: "ping" }
  | V3TextDeltaEvent
  | V3ReasoningDeltaEvent
  | V3ToolCallStartedEvent
  | V3ToolArgsDeltaEvent
  | V3ToolResultEvent
  | V3AssistantMessageDoneEvent
  | V3SkillActivatedEvent
  | V3ApprovalRequestEvent
  | V3ApprovalResolvedEvent
  | V3ArtifactEvent;

// ─── Sessions CRUD ─────────────────────────────────────────

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function createSession(
  title = "新对话",
  model?: string
): Promise<V3Session> {
  const res = await fetchWithAuth(`${API_BASE}/api/chat/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(model ? { title, model } : { title }),
  });
  return handleResponse<V3Session>(res);
}

export async function listModels(): Promise<V3ModelChoice[]> {
  const res = await fetchWithAuth(`${API_BASE}/api/chat/models`);
  const body = await handleResponse<{ models: V3ModelChoice[] }>(res);
  return body.models;
}

export async function patchSession(
  sessionId: string,
  patch: { title?: string; model?: string | null }
): Promise<V3Session> {
  // Empty string clears the model back to default; mirror that on the wire.
  const body: Record<string, string> = {};
  if (patch.title !== undefined) body.title = patch.title;
  if (patch.model !== undefined) body.model = patch.model ?? "";
  const res = await fetchWithAuth(`${API_BASE}/api/chat/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleResponse<V3Session>(res);
}

export async function listSessions(): Promise<V3Session[]> {
  const res = await fetchWithAuth(`${API_BASE}/api/chat/sessions`);
  return handleResponse<V3Session[]>(res);
}

export async function getSession(sessionId: string): Promise<V3SessionDetail> {
  const res = await fetchWithAuth(`${API_BASE}/api/chat/sessions/${sessionId}`);
  return handleResponse<V3SessionDetail>(res);
}

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetchWithAuth(`${API_BASE}/api/chat/sessions/${sessionId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

// ─── Send message ──────────────────────────────────────────

export async function sendMessage(
  sessionId: string,
  text: string,
  pageContext?: string | null
): Promise<{ ok: boolean; job_id?: string; status: string }> {
  // `page_context` tells the backend which URL the user was on when
  // they hit send, so the agent can answer "what's wrong with this
  // order?" without an upfront clarification roundtrip. Optional —
  // legacy callers (and the agent workspace, where the URL is the
  // chat) pass nothing and the backend skips the overlay.
  const body: { text: string; page_context?: string } = { text };
  if (pageContext) body.page_context = pageContext;
  const res = await fetchWithAuth(
    `${API_BASE}/api/chat/sessions/${sessionId}/messages`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }
  );
  return handleResponse(res);
}

export async function cancelRun(sessionId: string): Promise<void> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/chat/sessions/${sessionId}/cancel`,
    { method: "POST" }
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

// ─── HITL approval ─────────────────────────────────────────

export async function decideAction(
  actionId: number,
  decision: "approve" | "reject",
  notes = ""
): Promise<{ action_id: number; status: string; result?: Record<string, unknown> }> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/chat/actions/${actionId}/decide`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, notes }),
    }
  );
  return handleResponse(res);
}

// ─── Artifacts ─────────────────────────────────────────────

// Mirrors the 4-state schema returned by domains.masterdata.upload.preview_changes.
// The React viewer is the only consumer; values are display-only.
export interface UploadDiffFieldState {
  action: "change" | "unchanged" | "keep_db" | "set_new" | "immutable";
  db: unknown;
  excel: unknown;
  will_write: boolean;
}

export interface UploadDiffRow {
  row_index: number;
  product_code?: string | null;
  product_name?: string | null;
  match_status: string;
  matched_product_id?: number | null;
  fields: Record<string, UploadDiffFieldState>;
  will_write_fields: string[];
  reason?: string;  // present on `skip` rows
}

export interface UploadDiffArtifactPayload {
  batch_id: number;
  filename: string;
  summary: { create: number; update: number; skip: number; error: number };
  create: UploadDiffRow[];
  update: UploadDiffRow[];
  skip: UploadDiffRow[];
  error: UploadDiffRow[];
  truncated: boolean;
}

export async function getUploadDiffArtifact(
  batchId: number,
  limit: number = 200
): Promise<UploadDiffArtifactPayload> {
  const res = await fetchWithAuth(
    `${API_BASE}/api/artifacts/upload-diff/${batchId}?limit=${limit}`
  );
  return handleResponse<UploadDiffArtifactPayload>(res);
}

// ─── Data upload (used by chat for file attachments) ───────

export async function uploadDataFile(file: File): Promise<{
  batch_id: number;
  filename: string;
  status: string;
  total_rows: number;
}> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetchWithAuth(`${API_BASE}/api/data-upload/upload`, {
    method: "POST",
    body: fd,
  });
  return handleResponse(res);
}

// ─── SSE stream ────────────────────────────────────────────

/**
 * Open the chat session's SSE stream. Returns an abort function.
 *
 * The stream stays open for the life of the session; events arrive
 * whenever the agent runs (or when an admin force-closes it). Call
 * the abort function on unmount.
 */
export function streamSession(
  sessionId: string,
  onEvent: (event: V3StreamEvent) => void,
  onError: (err: Error) => void
): () => void {
  const controller = new AbortController();

  (async () => {
    try {
      const res = await fetchWithAuth(
        `${API_BASE}/api/chat/sessions/${sessionId}/stream`,
        { signal: controller.signal }
      );
      if (!res.ok) throw new Error(`SSE failed: HTTP ${res.status}`);

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith("data: ")) continue;
          let event: V3StreamEvent;
          try {
            event = JSON.parse(trimmed.slice(6));
          } catch {
            continue;
          }
          onEvent(event);
        }
      }
    } catch (err) {
      if (!controller.signal.aborted) {
        onError(err instanceof Error ? err : new Error(String(err)));
      }
    }
  })();

  return () => controller.abort();
}
