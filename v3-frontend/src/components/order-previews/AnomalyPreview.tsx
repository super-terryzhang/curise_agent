"use client";

import { AlertTriangle, CheckCircle2, CircleDashed, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface AnomalyPreviewProps {
  data: Record<string, unknown>;
}

interface PipelineStage {
  step: number;
  name: string;
  status: string;
  message?: string | null;
}

interface Finding {
  code: string;
  step: number;
  severity: "warning" | "error" | "blocking";
  scope: string;
  row_index?: number | null;
  source_line?: string | number | null;
  page?: string | number | null;
  product_code?: string | null;
  product_name?: string | null;
  message: string;
  suggestion?: string | null;
}

const STATUS_LABEL: Record<string, string> = {
  pending: "等待",
  running: "处理中",
  completed: "完成",
  completed_with_warnings: "完成·有提醒",
  completed_with_anomalies: "完成·有异常",
  needs_review: "需要处理",
  skipped: "暂未执行",
  failed: "失败",
};

function stageTone(status: string) {
  if (status === "completed") {
    return "border-emerald-500/25 bg-emerald-500/5 text-emerald-700 dark:text-emerald-400";
  }
  if (status.includes("warning") || status.includes("anomal")) {
    return "border-amber-500/25 bg-amber-500/5 text-amber-700 dark:text-amber-400";
  }
  if (status === "failed" || status === "needs_review") {
    return "border-destructive/25 bg-destructive/5 text-destructive";
  }
  return "border-border bg-muted/25 text-muted-foreground";
}

function locationOf(item: Finding) {
  const parts: string[] = [];
  if (item.page != null) parts.push(`第 ${item.page} 页`);
  if (item.source_line != null) parts.push(`原文行 ${item.source_line}`);
  else if (item.row_index != null) parts.push(`商品行 ${item.row_index}`);
  return parts.join(" · ");
}

export default function AnomalyPreview({ data }: AnomalyPreviewProps) {
  const pipeline = (data.pipeline || []) as PipelineStage[];
  const findings = (data.findings || []) as Finding[];
  const warnings = Number(
    data.warning_count || findings.filter((item) => item.severity === "warning").length,
  );
  const errors = Number(
    data.error_count || findings.filter((item) => item.severity === "error").length,
  );
  const blocking = Number(
    data.blocking_count || findings.filter((item) => item.severity === "blocking").length,
  );

  if (!pipeline.length && !findings.length) return <LegacyAnomalyPreview data={data} />;

  return (
    <div className="h-full overflow-y-auto bg-muted/10">
      <div className="border-b bg-background px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="text-sm font-semibold">自动处理与异常检测</div>
            <div className="mt-1 text-xs text-muted-foreground">
              系统自动完成正常数据，人工只处理下方明确标记的项目。
            </div>
          </div>
          <Badge variant={errors + blocking ? "destructive" : "secondary"}>
            {errors + blocking ? `${errors + blocking} 项待处理` : "无需人工处理"}
          </Badge>
        </div>
      </div>

      <div className="space-y-5 p-5">
        <section>
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-xs font-semibold tracking-wide">处理进度</h3>
            <span className="text-[11px] text-muted-foreground">共 8 个可验证环节</span>
          </div>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            {pipeline.map((stage) => {
              const completed = stage.status === "completed";
              const failed = stage.status === "failed" || stage.status === "needs_review";
              const Icon = completed
                ? CheckCircle2
                : failed
                  ? XCircle
                  : stage.status === "pending"
                    ? CircleDashed
                    : AlertTriangle;
              return (
                <div key={stage.step} className={cn("rounded-md border p-3", stageTone(stage.status))}>
                  <div className="flex items-start gap-2">
                    <Icon className="mt-0.5 h-4 w-4 shrink-0" />
                    <div className="min-w-0">
                      <div className="text-[11px] opacity-70">步骤 {stage.step}</div>
                      <div className="truncate text-xs font-medium text-foreground">{stage.name}</div>
                      <div className="mt-1 text-[11px]">{STATUS_LABEL[stage.status] || stage.status}</div>
                    </div>
                  </div>
                  {stage.message ? (
                    <p className="mt-2 text-[11px] leading-4 text-foreground/75">{stage.message}</p>
                  ) : null}
                </div>
              );
            })}
          </div>
        </section>

        <section>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h3 className="mr-auto text-xs font-semibold tracking-wide">需要关注的项目</h3>
            <Badge variant="outline">错误 {errors}</Badge>
            <Badge variant="outline">阻断 {blocking}</Badge>
            <Badge variant="outline">提醒 {warnings}</Badge>
          </div>

          {!findings.length ? (
            <Card className="border-emerald-500/20 bg-emerald-500/5">
              <CardContent className="flex items-center gap-3 py-5 text-sm text-emerald-700 dark:text-emerald-400">
                <CheckCircle2 className="h-5 w-5" />
                八个环节均未发现异常。
              </CardContent>
            </Card>
          ) : (
            <div className="overflow-hidden rounded-md border bg-background">
              {findings.map((item, index) => {
                const urgent = item.severity === "error" || item.severity === "blocking";
                const location = locationOf(item);
                return (
                  <div
                    key={`${item.code}-${item.step}-${item.source_line || index}`}
                    className="border-b p-4 last:border-b-0"
                  >
                    <div className="flex items-start gap-3">
                      {urgent ? (
                        <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                      ) : (
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge variant="outline" className="text-[10px]">步骤 {item.step}</Badge>
                          <span className="text-xs font-medium">
                            {item.product_name || item.product_code || "订单级检查"}
                          </span>
                          {location ? (
                            <span className="text-[11px] text-muted-foreground">{location}</span>
                          ) : null}
                        </div>
                        <p className="mt-1.5 text-xs leading-5 text-foreground/85">{item.message}</p>
                        {item.suggestion ? (
                          <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
                            建议：{item.suggestion}
                          </p>
                        ) : null}
                      </div>
                      <span className="shrink-0 font-mono text-[9px] text-muted-foreground">
                        {item.code}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function LegacyAnomalyPreview({ data }: AnomalyPreviewProps) {
  const price = (data.price_anomalies || []) as Array<Record<string, unknown>>;
  const quantity = (data.quantity_anomalies || []) as Array<Record<string, unknown>>;
  const completeness = (data.completeness_issues || []) as Array<Record<string, unknown> | string>;
  const items = [...price, ...quantity, ...completeness];

  return (
    <div className="space-y-3 p-5">
      <div className="text-sm font-semibold">异常检测</div>
      {!items.length ? (
        <div className="flex items-center gap-2 text-xs text-emerald-600">
          <CheckCircle2 className="h-4 w-4" />未发现异常
        </div>
      ) : (
        items.map((item, index) => {
          const record = typeof item === "string" ? { description: item } : item;
          return (
            <div key={index} className="rounded-md border bg-background p-3 text-xs">
              <div className="font-medium">{String(record.product_name || "数据问题")}</div>
              <div className="mt-1 text-muted-foreground">
                {String(record.description || record.issue || "请人工复核")}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}
