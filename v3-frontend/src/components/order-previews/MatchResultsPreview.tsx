"use client";

import { useState, useMemo } from "react";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Search, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { exportToCSV } from "@/lib/export-csv";

interface MatchResult {
  product_name?: string;
  product_code?: string;
  quantity?: number | null;
  unit?: string | null;
  match_status?: "matched" | "not_matched";
  match_score?: number;
  match_reason?: string;
  matched_product?: {
    product_name_en?: string;
    code?: string;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

interface Statistics {
  total?: number;
  matched?: number;
  not_matched?: number;
  match_rate?: number;
}

interface MatchResultsPreviewProps {
  data: Record<string, unknown>;
}

type StatusFilter = "all" | "matched" | "not_matched";

const STATUS_CONFIG: Record<string, { label: string; color: string; borderColor: string }> = {
  matched: { label: "已匹配", color: "text-emerald-500", borderColor: "border-l-emerald-500/40" },
  not_matched: { label: "未匹配", color: "text-destructive", borderColor: "border-l-destructive/40" },
};

export default function MatchResultsPreview({ data }: MatchResultsPreviewProps) {
  const matchResults = (data.match_results || []) as MatchResult[];
  const statistics = (data.statistics || {}) as Statistics;

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  const total = statistics.total ?? matchResults.length;
  const matchedCount = statistics.matched ?? matchResults.filter((r) => r.match_status === "matched").length;
  const unmatchedCount = statistics.not_matched ?? matchResults.filter((r) => r.match_status === "not_matched").length;
  const matchRate = statistics.match_rate ?? (total > 0 ? Math.round((matchedCount / total) * 100) : 0);

  const filteredResults = useMemo(() => {
    let results = matchResults;
    if (statusFilter !== "all") {
      results = results.filter((r) => r.match_status === statusFilter);
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      results = results.filter(
        (r) =>
          (r.product_name || "").toLowerCase().includes(q) ||
          (r.product_code || "").toLowerCase().includes(q) ||
          (r.matched_product?.product_name_en || "").toLowerCase().includes(q) ||
          (r.matched_product?.code || "").toLowerCase().includes(q)
      );
    }
    return results;
  }, [matchResults, statusFilter, search]);

  const filterButtons: { key: StatusFilter; label: string; count: number }[] = [
    { key: "all", label: "全部", count: total },
    { key: "matched", label: "已匹配", count: matchedCount },
    { key: "not_matched", label: "未匹配", count: unmatchedCount },
  ];

  return (
    <div className="h-full flex flex-col">
      {/* Stats bar */}
      <div className="shrink-0 px-4 py-3 border-b border-border/50">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold">{total}</span>
            <span className="text-[10px] text-muted-foreground">总计</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-500" />
            <span className="text-xs font-medium text-emerald-500">{matchedCount} 已匹配</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-destructive" />
            <span className="text-xs font-medium text-destructive">{unmatchedCount} 未匹配</span>
          </div>
          <div className="flex-1" />
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-muted-foreground">匹配率</span>
            <Progress value={matchRate} className="w-24 h-1.5" />
            <span className="text-xs font-medium">{matchRate}%</span>
          </div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="shrink-0 px-4 py-2 border-b border-border/50 flex items-center gap-3">
        <div className="relative max-w-xs">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索产品名、代码..."
            className="pl-9 h-8 text-xs w-56"
          />
        </div>
        <div className="flex items-center gap-1">
          {filterButtons.map((btn) => (
            <button
              key={btn.key}
              onClick={() => setStatusFilter(btn.key)}
              className={cn(
                "px-2.5 py-1 rounded-full text-[10px] font-medium transition-colors",
                statusFilter === btn.key
                  ? "bg-primary/10 text-primary"
                  : "bg-muted text-muted-foreground hover:text-foreground"
              )}
            >
              {btn.label} ({btn.count})
            </button>
          ))}
        </div>
        <Button
          variant="outline"
          size="sm"
          className="h-8 text-xs ml-auto"
          onClick={() => {
            exportToCSV(
              ["产品名称", "产品代码", "数量", "单位", "匹配状态", "匹配产品", "匹配代码", "原因/备注"],
              filteredResults.map((r) => [
                r.product_name || "",
                r.product_code || "",
                r.quantity ?? "",
                r.unit || "",
                STATUS_CONFIG[r.match_status || "not_matched"]?.label || "",
                r.matched_product?.product_name_en || "",
                r.matched_product?.code || "",
                r.match_reason || "",
              ]),
              `匹配结果_${new Date().toISOString().slice(0, 10)}.csv`
            );
          }}
        >
          <Download className="mr-1 h-3 w-3" /> 导出 CSV
        </Button>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="w-8 text-[10px]">#</TableHead>
              <TableHead className="text-[10px]">订单产品</TableHead>
              <TableHead className="text-[10px] w-24">代码</TableHead>
              <TableHead className="text-[10px] w-16">数量</TableHead>
              <TableHead className="text-[10px] w-20">状态</TableHead>
              <TableHead className="text-[10px]">匹配产品 / 未匹配原因</TableHead>
              <TableHead className="text-[10px] w-24">匹配代码</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filteredResults.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="text-center py-8 text-muted-foreground text-xs">
                  {search || statusFilter !== "all" ? "没有匹配的结果" : "暂无数据"}
                </TableCell>
              </TableRow>
            ) : (
              filteredResults.map((item, i) => {
                const status = item.match_status || "not_matched";
                const config = STATUS_CONFIG[status] || STATUS_CONFIG.not_matched;
                const isMatched = status === "matched";

                return (
                  <TableRow key={i} className={cn("border-l-2 align-top", config.borderColor)}>
                    <TableCell className="text-[10px] text-muted-foreground pt-3">{i + 1}</TableCell>
                    <TableCell className="text-xs pt-3 max-w-[220px]">
                      <div className="font-medium leading-snug">{item.product_name || "-"}</div>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground font-mono pt-3">{item.product_code || "-"}</TableCell>
                    <TableCell className="text-xs pt-3">
                      {item.quantity != null ? item.quantity : "-"}
                      {item.unit && <span className="text-muted-foreground ml-0.5">{item.unit}</span>}
                    </TableCell>
                    <TableCell className="pt-3">
                      <Badge variant="outline" className={cn("text-[10px] gap-1", config.color)}>
                        <span className={cn("w-1.5 h-1.5 rounded-full", isMatched ? "bg-emerald-500" : "bg-destructive")} />
                        {config.label}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs pt-3 max-w-[280px]">
                      {isMatched ? (
                        <span className="text-foreground/80">{item.matched_product?.product_name_en || "-"}</span>
                      ) : (
                        <span className="text-destructive/80 leading-snug">
                          {item.match_reason || "未在供应商目录中找到"}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground font-mono pt-3">
                      {isMatched ? (item.matched_product?.code || "-") : "-"}
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
