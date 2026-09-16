"use client";

/**
 * ArtifactPanel — dispatches a v3 `artifact` SSE event payload to the
 * matching React component.
 *
 * The agent picks `component` from the backend's catalog (see
 * agent/runtime/artifacts.py). We map that name here. Adding a new
 * artifact = add one entry to the registry below + one React component.
 *
 * Unknown component names render a defensive placeholder rather than
 * throwing — the SSE event has already been seen, swallowing it would
 * be worse than admitting we don't have a renderer yet.
 */

import type { ChatArtifact } from "@/lib/v3-chat-store";
import { GenericTable } from "./GenericTable";
import { InquiryBatchCard } from "./InquiryBatchCard";
import { UploadDiffViewer } from "./UploadDiffViewer";

interface Props {
  artifact: ChatArtifact;
}

export function ArtifactPanel({ artifact }: Props) {
  return (
    <div className="border rounded-md bg-background overflow-hidden">
      <div className="px-3 py-2 bg-zinc-50 dark:bg-zinc-900 border-b border-border/40 text-xs flex items-center justify-between">
        <span className="font-medium">🖼️ {artifact.narration}</span>
        <span className="text-muted-foreground font-mono text-[10px]">
          {artifact.component}
        </span>
      </div>
      <div className="p-2">
        <ArtifactBody artifact={artifact} />
      </div>
    </div>
  );
}

function ArtifactBody({ artifact }: Props) {
  const { component, data } = artifact;
  switch (component) {
    case "upload_diff_viewer": {
      const batchId = typeof data.batch_id === "number" ? data.batch_id : null;
      if (batchId === null) {
        return (
          <p className="text-xs text-destructive">
            upload_diff_viewer: missing or non-numeric batch_id
          </p>
        );
      }
      // `view` is an opaque dict the backend already validated against
      // the catalog enums; pass through to the component.
      const view =
        data.view && typeof data.view === "object"
          ? (data.view as Parameters<typeof UploadDiffViewer>[0]["view"])
          : undefined;
      return <UploadDiffViewer batchId={batchId} view={view} />;
    }

    case "generic_table": {
      const title = typeof data.title === "string" ? data.title : "";
      const columns = Array.isArray(data.columns)
        ? (data.columns as Array<{ key: string; label: string; align?: "left" | "right" | "center" }>)
        : [];
      const rows = Array.isArray(data.rows)
        ? (data.rows as Array<Record<string, unknown>>)
        : [];
      const caption = typeof data.caption === "string" ? data.caption : undefined;
      const highlightKey = typeof data.highlight_key === "string" ? data.highlight_key : undefined;
      return (
        <GenericTable
          title={title}
          columns={columns}
          rows={rows}
          caption={caption}
          highlightKey={highlightKey}
        />
      );
    }

    case "narration_only":
      // Pure-text artifact — narration is already in the title bar.
      return null;

    case "inquiry_batch_card": {
      const orderId = typeof data.order_id === "number" ? data.order_id : null;
      if (orderId === null) {
        return (
          <p className="text-xs text-destructive">
            inquiry_batch_card: missing or non-numeric order_id
          </p>
        );
      }
      return <InquiryBatchCard orderId={orderId} />;
    }

    default:
      return (
        <p className="text-xs text-muted-foreground">
          未注册的 artifact 组件:{" "}
          <span className="font-mono">{component}</span>
        </p>
      );
  }
}
