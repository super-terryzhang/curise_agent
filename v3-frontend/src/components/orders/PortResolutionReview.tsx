"use client";

import { AlertTriangle, Bot, Check, Loader2, Pencil } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { PortResolutionState } from "@/lib/orders-api";
import { portResolutionPresentation } from "@/lib/port-resolution-view";

interface PortResolutionReviewProps {
  state: PortResolutionState | null | undefined;
  canonicalPortName?: string | null;
  onConfirm?: () => void | Promise<void>;
  onChange?: () => void;
  busy?: boolean;
  className?: string;
}

interface PortResolutionBadgeProps {
  state: PortResolutionState | null | undefined;
  className?: string;
}

const toneClasses = {
  warning: "border-amber-300 bg-amber-50 text-amber-800",
  success: "border-emerald-300 bg-emerald-50 text-emerald-800",
  neutral: "border-slate-300 bg-slate-50 text-slate-700",
  danger: "border-red-300 bg-red-50 text-red-800",
} as const;

export function PortResolutionBadge({
  state,
  className,
}: PortResolutionBadgeProps) {
  const presentation = portResolutionPresentation(state);
  if (!presentation) return null;

  return (
    <Badge
      variant="outline"
      className={cn(
        "rounded-[3px]",
        toneClasses[presentation.tone],
        className,
      )}
    >
      {presentation.tone === "danger" ? (
        <AlertTriangle aria-hidden="true" />
      ) : (
        <Bot aria-hidden="true" />
      )}
      {presentation.label}
    </Badge>
  );
}

export function PortResolutionReview({
  state,
  canonicalPortName,
  onConfirm,
  onChange,
  busy = false,
  className,
}: PortResolutionReviewProps) {
  const presentation = portResolutionPresentation(state);
  if (!state || !presentation) return null;

  return (
    <section
      className={cn(
        "space-y-2 rounded-[3px] border border-slate-200 bg-white p-3",
        className,
      )}
      aria-label="港口识别审核"
    >
      <div className="flex flex-wrap items-center gap-2">
        <PortResolutionBadge state={state} />
        {canonicalPortName ? (
          <span className="text-sm font-medium">{canonicalPortName}</span>
        ) : null}
      </div>

      <dl className="grid gap-x-3 gap-y-1 text-xs sm:grid-cols-[88px_1fr]">
        <dt className="text-muted-foreground">PO 原始地点</dt>
        <dd className="break-words">{state.source_destination || "—"}</dd>
        {state.reason ? (
          <>
            <dt className="text-muted-foreground">判断说明</dt>
            <dd className="break-words">{state.reason}</dd>
          </>
        ) : null}
      </dl>

      {presentation.canConfirm || presentation.canChange ? (
        <div className="flex flex-wrap gap-2 pt-1">
          {presentation.canConfirm && onConfirm ? (
            <Button size="xs" onClick={() => void onConfirm()} disabled={busy}>
              {busy ? <Loader2 className="animate-spin" /> : <Check />}
              确认此港口
            </Button>
          ) : null}
          {presentation.canChange && onChange ? (
            <Button
              size="xs"
              variant="outline"
              onClick={onChange}
              disabled={busy}
            >
              <Pencil />
              选择其他港口
            </Button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
