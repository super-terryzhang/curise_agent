"use client";

/**
 * InquiryBatchCard — summary card for a batch of generated supplier
 * inquiry Excel files. Reads its data from
 * `GET /api/artifacts/inquiry-batch/{order_id}` (artifact endpoint),
 * which surfaces the live inquiry state at request time. That live
 * read means a regenerate-after-card-dispatch correctly shows the
 * new file URL without needing the agent to re-dispatch the artifact.
 *
 * Contract with the backend response (see
 * v3_backend/apps/http/artifacts.py:get_inquiry_batch_artifact):
 *   { order_id, po_number, status, supplier_count, files: [...] }
 *
 * Cross-user isolation is enforced server-side: 404 on non-owned
 * orders, so this component renders an inline "未找到 / 无权访问"
 * fallback in that case.
 */

import { useEffect, useState } from "react";
import { Download, ExternalLink, FileSpreadsheet, Loader2 } from "lucide-react";

import { fetchWithAuth } from "@/lib/fetch-with-auth";

interface InquiryBatchFile {
  supplier_id: number;
  supplier_name: string;
  filename: string | null;
  download_url: string | null;
  status: string;
  error_message: string | null;
}

interface InquiryBatchResponse {
  order_id: number;
  po_number: string | null;
  status: string;
  supplier_count: number;
  files: InquiryBatchFile[];
}

interface Props {
  orderId: number;
}

export function InquiryBatchCard({ orderId }: Props) {
  const [data, setData] = useState<InquiryBatchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchWithAuth(`/api/artifacts/inquiry-batch/${orderId}`)
      .then(async (res) => {
        if (cancelled) return;
        if (!res.ok) {
          if (res.status === 404) {
            setError("未找到该订单或无权访问");
          } else {
            setError(`加载失败 (${res.status})`);
          }
          return;
        }
        const body = (await res.json()) as InquiryBatchResponse;
        if (!cancelled) setData(body);
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [orderId]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 px-3 py-4 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        加载询价单文件...
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
        {error}
      </div>
    );
  }

  if (!data) return null;

  const successfulFiles = data.files.filter((f) => !!f.download_url);
  const orderLabel = data.po_number ? `订单 #${data.po_number}` : `订单 #${data.order_id}`;

  return (
    <div className="space-y-3 text-xs">
      {/* Header summary */}
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="text-sm font-medium">{orderLabel}</div>
          <div className="text-[11px] text-muted-foreground">
            已生成 {successfulFiles.length} 份询价单
            {data.files.length > successfulFiles.length &&
              ` · ${data.files.length - successfulFiles.length} 个失败`}
          </div>
        </div>
        <a
          href={`/dashboard/orders/${data.order_id}`}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
        >
          在订单详情页查看
          <ExternalLink className="h-3 w-3" />
        </a>
      </div>

      {/* File list */}
      {data.files.length === 0 ? (
        <div className="rounded-md border border-dashed px-3 py-4 text-center text-[11px] text-muted-foreground">
          还没有生成任何文件
        </div>
      ) : (
        <ul className="divide-y rounded-md border">
          {data.files.map((file) => (
            <li
              key={file.supplier_id}
              className="flex items-center gap-2 px-3 py-2"
            >
              <FileSpreadsheet
                className={
                  file.download_url
                    ? "h-3.5 w-3.5 shrink-0 text-foreground"
                    : "h-3.5 w-3.5 shrink-0 text-muted-foreground"
                }
              />
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{file.supplier_name}</div>
                <div className="truncate text-[10px] text-muted-foreground">
                  {file.filename ?? <FileStatus status={file.status} error={file.error_message} />}
                </div>
              </div>
              {file.download_url ? (
                <a
                  href={file.download_url}
                  download
                  className="inline-flex h-7 items-center gap-1 rounded-md border bg-background px-2 text-[11px] hover:bg-muted"
                  title="下载"
                >
                  <Download className="h-3 w-3" />
                  下载
                </a>
              ) : (
                <span className="text-[10px] text-muted-foreground">—</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function FileStatus({
  status,
  error,
}: {
  status: string;
  error: string | null;
}) {
  if (error) return <span className="text-destructive">{error}</span>;
  switch (status) {
    case "pending":
      return <span>排队中</span>;
    case "running":
      return <span>生成中</span>;
    case "failed":
      return <span className="text-destructive">生成失败</span>;
    default:
      return <span>{status}</span>;
  }
}
