import type { PortResolutionState } from "./orders-api";

export type PortResolutionTone =
  | "warning"
  | "success"
  | "neutral"
  | "danger";

export interface PortResolutionPresentation {
  label: string;
  tone: PortResolutionTone;
  canConfirm: boolean;
  canChange: boolean;
}

export function portResolutionPresentation(
  state: PortResolutionState | null | undefined,
): PortResolutionPresentation | null {
  if (!state) return null;

  if (state.method === "manual" || state.status === "overridden") {
    return {
      label: "人工选择",
      tone: "neutral",
      canConfirm: false,
      canChange: false,
    };
  }

  if (state.status === "pending_review") {
    return {
      label: "AI 匹配 · 待人工确认",
      tone: "warning",
      canConfirm: true,
      canChange: true,
    };
  }

  if (state.status === "confirmed") {
    return {
      label: "AI 匹配 · 已确认",
      tone: "success",
      canConfirm: false,
      canChange: false,
    };
  }

  return {
    label: "无法确定港口",
    tone: "danger",
    canConfirm: false,
    canChange: true,
  };
}
