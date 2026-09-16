/**
 * Adapter contract tests for `assistant-ui-adapter.ts`.
 *
 * Goal: pin each V3Message → ThreadMessageLike transformation so that
 * future assistant-ui upgrades or our v3-chat-api schema changes surface
 * a deterministic regression instead of a vague UI bug.
 *
 * Each case below documents a real shape that flows through useV3Chat in
 * production. If you change `v3MessageToAssistantMessage` and a test
 * fails, ask "did I really mean to alter that rendering contract?"
 * before updating the expectation.
 */

import { describe, expect, it } from "vitest";

import {
  attachApprovalsToLastAssistant,
  buildAssistantMessages,
  v3MessageToAssistantMessage,
  type ApprovalDataPart,
} from "./assistant-ui-adapter";
import type { V3Message } from "./v3-chat-api";
import type { PendingApproval } from "./v3-chat-store";

// ─── Fixture helpers ────────────────────────────────────────

function userMsg(id: number, content: string): V3Message {
  return {
    id,
    session_id: "s",
    sequence: id,
    role: "user",
    parts: [
      {
        type: "raw",
        schema: "openai-chat-completion",
        data: { role: "user", content },
      },
    ],
    model: null,
    created_at: null,
  };
}

function assistantMsg(
  id: number,
  content: string | null,
  toolCalls?: Array<{ id: string; name: string; args: string }>,
): V3Message {
  return {
    id,
    session_id: "s",
    sequence: id,
    role: "assistant",
    parts: [
      {
        type: "raw",
        schema: "openai-chat-completion",
        data: {
          role: "assistant",
          content,
          tool_calls: toolCalls?.map((tc) => ({
            id: tc.id,
            type: "function" as const,
            function: { name: tc.name, arguments: tc.args },
          })),
        },
      },
    ],
    model: "gemini-3.5-flash",
    created_at: null,
  };
}

function toolMsg(id: number, callId: string, result: string): V3Message {
  return {
    id,
    session_id: "s",
    sequence: id,
    role: "tool",
    parts: [
      {
        type: "raw",
        schema: "openai-chat-completion",
        data: {
          role: "tool",
          content: result,
          tool_call_id: callId,
          name: "search_db",
        },
      },
    ],
    model: null,
    created_at: null,
  };
}

function approval(actionId: number, summary: string): PendingApproval {
  return {
    session_id: "s",
    action_id: actionId,
    action: "commit_upload_batch",
    target_kind: "upload_batch",
    target_id: actionId,
    payload: { batch_id: actionId },
    summary,
    status: "pending",
  };
}

// ─── v3MessageToAssistantMessage ────────────────────────────

describe("v3MessageToAssistantMessage", () => {
  it("converts user message with text content", () => {
    const out = v3MessageToAssistantMessage(userMsg(1, "hello"));
    expect(out).toEqual({
      id: "1",
      role: "user",
      content: [{ type: "text", text: "hello" }],
    });
  });

  it("converts assistant message with text only", () => {
    const out = v3MessageToAssistantMessage(assistantMsg(2, "hi there"));
    expect(out).toEqual({
      id: "2",
      role: "assistant",
      content: [{ type: "text", text: "hi there" }],
    });
  });

  it("converts assistant message with tool_calls (parsed args)", () => {
    const out = v3MessageToAssistantMessage(
      assistantMsg(3, null, [
        { id: "call_1", name: "query_db", args: '{"sql": "SELECT 1"}' },
      ]),
    );
    expect(out?.content).toEqual([
      {
        type: "tool-call",
        toolCallId: "call_1",
        toolName: "query_db",
        args: { sql: "SELECT 1" },
      },
    ]);
  });

  it("falls back to _raw arg when tool_call.arguments is mid-stream invalid JSON", () => {
    const out = v3MessageToAssistantMessage(
      assistantMsg(4, null, [
        // Streaming arg fragment — not yet valid JSON.
        { id: "call_2", name: "query_db", args: '{"sql": "SEL' },
      ]),
    );
    const part = (out?.content as unknown as Array<{ type: string; args?: unknown }>)?.[0];
    expect(part?.type).toBe("tool-call");
    expect(part?.args).toEqual({ _raw: '{"sql": "SEL' });
  });

  it("preserves text BEFORE tool_calls in part order", () => {
    const out = v3MessageToAssistantMessage(
      assistantMsg(5, "Looking that up...", [
        { id: "c", name: "query", args: "{}" },
      ]),
    );
    expect((out?.content as unknown as Array<{ type: string }>)[0].type).toBe("text");
    expect((out?.content as unknown as Array<{ type: string }>)[1].type).toBe("tool-call");
  });

  it("converts tool result into a tool-call part carrying `result`", () => {
    const out = v3MessageToAssistantMessage(
      toolMsg(6, "call_1", "row count: 53"),
    );
    expect(out?.role).toBe("assistant");
    const part = (out?.content as unknown as Array<{ type: string; result?: string }>)[0];
    expect(part.type).toBe("tool-call");
    expect(part.result).toBe("row count: 53");
  });

  it("drops system messages (returns null)", () => {
    const sys: V3Message = {
      id: 7,
      session_id: "s",
      sequence: 7,
      role: "system",
      parts: [
        {
          type: "raw",
          schema: "openai-chat-completion",
          data: { role: "system", content: "You are…" },
        },
      ],
      model: null,
      created_at: null,
    };
    expect(v3MessageToAssistantMessage(sys)).toBeNull();
  });

  it("returns a placeholder text part for an assistant message with no content (prevents empty bubble crashes)", () => {
    const out = v3MessageToAssistantMessage(assistantMsg(8, null));
    expect(out?.content).toEqual([{ type: "text", text: "" }]);
  });

  it("surfaces reasoning_content as a `reasoning` part BEFORE the text answer", () => {
    // Our backend persists reasoning under `reasoning_content` on the
    // raw part data (non-standard OpenAI field). The renderer relies on
    // the order: reasoning first, then conclusion text — flip this and
    // the UI shows the answer before the chain-of-thought, which
    // confused customers in early v3 builds.
    const msg: V3Message = {
      id: 9,
      session_id: "s",
      sequence: 9,
      role: "assistant",
      parts: [
        {
          type: "raw",
          schema: "openai-chat-completion",
          data: {
            role: "assistant",
            content: "答案：53 行",
            reasoning_content: "需要查表确认 SKU 数量...",
          } as unknown as V3Message["parts"][number]["data"],
        },
      ],
      model: "gemini-3.5-flash",
      created_at: null,
    };
    const out = v3MessageToAssistantMessage(msg);
    const parts = out?.content as unknown as Array<{
      type: string;
      text?: string;
    }>;
    expect(parts[0]).toEqual({ type: "reasoning", text: "需要查表确认 SKU 数量..." });
    expect(parts[1]).toEqual({ type: "text", text: "答案：53 行" });
  });

  it("surfaces skills_activated as a `data-skills` part at the top of the assistant turn", () => {
    const msg: V3Message = {
      id: 10,
      session_id: "s",
      sequence: 10,
      role: "assistant",
      parts: [
        {
          type: "raw",
          schema: "openai-chat-completion",
          data: {
            role: "assistant",
            content: "好",
            skills_activated: [
              { name: "master-data-upload", description: "Excel 主数据上传" },
            ],
          } as unknown as V3Message["parts"][number]["data"],
        },
      ],
      model: null,
      created_at: null,
    };
    const out = v3MessageToAssistantMessage(msg);
    const parts = out?.content as unknown as Array<{
      type: string;
      name?: string;
    }>;
    expect(parts[0]).toEqual({
      type: "data",
      name: "skills",
      data: { skills: [{ name: "master-data-upload", description: "Excel 主数据上传" }] },
    });
  });
});

// ─── attachApprovalsToLastAssistant ─────────────────────────

describe("attachApprovalsToLastAssistant", () => {
  it("returns input unchanged when no approvals pending", () => {
    const msgs = [
      v3MessageToAssistantMessage(userMsg(1, "q"))!,
      v3MessageToAssistantMessage(assistantMsg(2, "a"))!,
    ];
    expect(attachApprovalsToLastAssistant(msgs, [])).toEqual(msgs);
  });

  it("appends approval as a data part on the trailing assistant message", () => {
    const msgs = [
      v3MessageToAssistantMessage(userMsg(1, "q"))!,
      v3MessageToAssistantMessage(assistantMsg(2, "ready"))!,
    ];
    const out = attachApprovalsToLastAssistant(msgs, [approval(29, "提交批次 #29")]);
    const lastContent = out[out.length - 1].content as unknown as Array<{
      type: string;
      name?: string;
      data?: ApprovalDataPart;
    }>;
    expect(lastContent[lastContent.length - 1]).toEqual({
      type: "data",
      name: "approval",
      data: expect.objectContaining({
        kind: "approval",
        action_id: 29,
        action: "commit_upload_batch",
        status: "pending",
      }),
    });
  });

  it("creates a synthetic assistant message when there is none yet to host the approval", () => {
    const out = attachApprovalsToLastAssistant([], [approval(7, "x")]);
    expect(out).toHaveLength(1);
    expect(out[0].role).toBe("assistant");
    expect((out[0].content as unknown as Array<{ type: string }>)[0].type).toBe("data");
  });

  it("attaches multiple approvals each as its own data part", () => {
    const msgs = [v3MessageToAssistantMessage(assistantMsg(1, "ready"))!];
    const out = attachApprovalsToLastAssistant(msgs, [
      approval(1, "a"),
      approval(2, "b"),
    ]);
    const parts = out[0].content as unknown as Array<{ type: string }>;
    const dataCount = parts.filter((p) => p.type === "data").length;
    expect(dataCount).toBe(2);
  });
});

// ─── buildAssistantMessages (full pipeline) ─────────────────

describe("buildAssistantMessages", () => {
  it("end-to-end: drops system, converts user/assistant/tool, attaches approval", () => {
    const out = buildAssistantMessages(
      [
        userMsg(1, "do it"),
        assistantMsg(2, "ok, calling tool"),
        toolMsg(3, "c1", "result"),
      ],
      [approval(99, "confirm?")],
    );
    expect(out.map((m) => m.role)).toEqual([
      "user",
      "assistant",
      "assistant",
    ]);
    // approval was attached to the last assistant (the tool-result one)
    const lastParts = out[out.length - 1].content as unknown as Array<{ type: string }>;
    expect(lastParts.some((p) => p.type === "data")).toBe(true);
  });
});
