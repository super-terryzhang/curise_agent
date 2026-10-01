"use client";

import { CheckCircle2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { CommitResult } from "@/lib/product-upload-api";
import { UploadHistory } from "../product-upload/UploadHistory";

interface CompletionStepProps {
  result: CommitResult;
  onReset: () => void;
  showHistory?: boolean;
}

export function CompletionStep({ result, onReset, showHistory = true }: CompletionStepProps) {
  const metrics = [
    { label: "已更新", value: result.updated },
    { label: "无变化", value: result.skipped },
    { label: "失败", value: result.errors },
  ];

  return (
    <section>
      <div className="rounded-md border border-emerald-200 bg-emerald-50 px-5 py-5 text-emerald-900">
        <div className="flex items-center gap-3"><CheckCircle2 className="size-6" /><h2 className="text-base font-semibold">更新已完成</h2></div>
        <p className="mt-2 text-sm">系统已完成写入并保留本次上传记录；如需撤销，可在最近上传中按批次回滚。</p>
        <div className="mt-4 grid max-w-xl grid-cols-3 overflow-hidden rounded-md border border-emerald-200 bg-background">
          {metrics.map((metric) => (
            <div key={metric.label} className="border-r px-4 py-3 text-center last:border-r-0"><div className="text-lg font-semibold">{metric.value}</div><div className="text-xs text-muted-foreground">{metric.label}</div></div>
          ))}
        </div>
        <Button className="mt-5" onClick={onReset}>继续更新其他产品</Button>
      </div>
      {showHistory && <UploadHistory />}
    </section>
  );
}
