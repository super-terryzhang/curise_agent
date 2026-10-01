import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

export function WorkflowStepBar({
  labels,
  current,
}: {
  labels: readonly string[];
  current: number;
}) {
  return (
    <ol
      aria-label="流程进度"
      className="grid border-b bg-muted/20 px-5 py-4"
      style={{ gridTemplateColumns: `repeat(${labels.length}, minmax(0, 1fr))` }}
    >
      {labels.map((label, index) => {
        const number = index + 1;
        const done = number < current;
        const active = number === current;
        return (
          <li
            key={label}
            aria-current={active ? "step" : undefined}
            className="flex min-w-0 items-center"
          >
            <div
              className={cn(
                "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
                done && "border-foreground bg-foreground text-background",
                active && "border-primary bg-primary text-primary-foreground",
                !done && !active && "bg-background text-muted-foreground",
              )}
            >
              {done ? <Check className="h-3.5 w-3.5" /> : number}
            </div>
            <span
              className={cn(
                "ml-2 truncate text-xs",
                active ? "font-semibold" : "text-muted-foreground",
              )}
            >
              {label}
            </span>
            {number < labels.length && <div className="mx-3 h-px flex-1 bg-border" />}
          </li>
        );
      })}
    </ol>
  );
}
