import { describe, expect, it } from "vitest";

import type { PortResolutionState } from "./orders-api";
import { portResolutionPresentation } from "./port-resolution-view";

const state = (
  overrides: Partial<PortResolutionState>,
): PortResolutionState => ({
  method: "llm",
  status: "pending_review",
  source_destination: "OSAKA",
  source_port_code: null,
  suggested_port_id: 21,
  final_port_id: 21,
  model: "gemini-2.5-flash",
  prompt_version: "port-resolution-v1",
  decision_id: "decision-1",
  reason: "The destination text corresponds to Osaka.",
  decided_at: "2026-09-27T00:00:00Z",
  failure_code: null,
  reviewed_by: null,
  reviewed_at: null,
  ...overrides,
});

describe("portResolutionPresentation", () => {
  it("renders no badge or actions for legacy orders without resolution state", () => {
    expect(portResolutionPresentation(null)).toBeNull();
  });

  it("marks an LLM result as pending and offers confirm and change", () => {
    expect(portResolutionPresentation(state({}))).toEqual({
      label: "AI 匹配 · 待人工确认",
      tone: "warning",
      canConfirm: true,
      canChange: true,
    });
  });

  it("marks a confirmed LLM result as complete without more actions", () => {
    expect(
      portResolutionPresentation(state({ status: "confirmed" })),
    ).toEqual({
      label: "AI 匹配 · 已确认",
      tone: "success",
      canConfirm: false,
      canChange: false,
    });
  });

  it.each([
    state({ status: "overridden", method: "manual" }),
    state({ status: "confirmed", method: "manual" }),
  ])("marks manually selected ports without AI actions", (resolution) => {
    expect(portResolutionPresentation(resolution)).toEqual({
      label: "人工选择",
      tone: "neutral",
      canConfirm: false,
      canChange: false,
    });
  });

  it("keeps unresolved orders editable but not confirmable", () => {
    expect(
      portResolutionPresentation(
        state({
          status: "unresolved",
          suggested_port_id: null,
          final_port_id: null,
          failure_code: "no_match",
        }),
      ),
    ).toEqual({
      label: "无法确定港口",
      tone: "danger",
      canConfirm: false,
      canChange: true,
    });
  });
});
