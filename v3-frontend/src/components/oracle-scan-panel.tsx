"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, RefreshCw } from "lucide-react";
import { fetchWithAuth } from "@/lib/fetch-with-auth";
import { getUser } from "@/lib/auth";
import { oracleIssueMessage, oracleRunErrorMessage } from "@/lib/oracle-scan-messages";
import { Button } from "@/components/ui/button";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";
type Item = { po_number: string; status: string; stage?: string; document_id?: number; order_id?: number; inquiry_id?: number; issues?: { code: string; field?: string | null }[]; error_code?: string };
type Run = { id: number; trigger: string; status: string; created_at: string; finished_at?: string; error_code?: string; items: Item[] };
type State = { enabled: boolean; next_scan_at: string | null; runs: Run[]; files: Item[] };
const labels: Record<string, string> = { queued: "等待扫描", running: "扫描中", processing: "处理中", completed: "已完成", completed_with_issues: "扫描完成 · 有待处理项", failed: "失败", needs_review: "需人工处理", historical_pending: "已有 OPEN · 待确认导入", deferred: "等待下一轮", download: "下载中", analysis: "分析中", order: "创建订单", matching: "匹配商品", inquiry: "生成询价" };
const date = (value?: string | null) => value ? new Date(/Z$|[+-]\d\d:\d\d$/.test(value) ? value : value + "Z").toLocaleString("zh-CN") : "尚未扫描";

export function OracleScanPanel({ onUpdated }: { onUpdated: () => void }) {
  const [state, setState] = useState<State | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [canScan, setCanScan] = useState(false);
  useEffect(() => { setCanScan(["superadmin", "admin", "employee", "finance"].includes(getUser()?.role || "")); }, []);
  const load = useCallback(async () => {
    try {
      const response = await fetchWithAuth(`${API}/api/oracle/scans`);
      if (!response.ok) throw new Error("扫描状态暂时无法读取");
      setState(await response.json()); setError("");
    } catch (e) { setError(e instanceof Error ? e.message : "扫描状态暂时无法读取"); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const latest = state?.runs[0];
  const active = latest?.status === "running" || latest?.status === "queued";
  useEffect(() => {
    const timer = setInterval(() => { void load(); if (active) onUpdated(); }, active ? 5000 : 60000);
    return () => clearInterval(timer);
  }, [load, active, onUpdated]);
  const scan = async (poNumber?: string) => {
    setBusy(true); setError("");
    try {
      const response = await fetchWithAuth(poNumber ? `${API}/api/oracle/imports/${encodeURIComponent(poNumber)}` : `${API}/api/oracle/scans`, { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "扫描启动失败");
      if (result.status === "failed") throw new Error(oracleRunErrorMessage(result.error_code || "SCAN_UNKNOWN"));
      setExpanded(true); await load(); onUpdated();
    } catch (e) { setError(e instanceof Error ? e.message : "扫描启动失败"); }
    finally { setBusy(false); }
  };
  const items = new Map<string, Item>();
  for (const run of [...(state?.runs || [])].reverse()) for (const item of run.items || []) {
    items.set(item.po_number, item);
  }
  for (const file of state?.files || []) {
    const item = items.get(file.po_number);
    if (!item || ["historical_pending", "processing"].includes(item.status)) items.set(file.po_number, file);
  }
  return <section className="mb-5 rounded-lg border bg-background p-4" aria-label="Oracle 自动收单">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h2 className="text-sm font-semibold">Oracle 自动收单</h2>
        <p className="mt-1 text-xs text-muted-foreground">{state?.enabled ? "每小时整点扫描 · 下载后自动分析、建单并生成询价" : "自动扫描尚未启用"}</p>
      </div>
      <Button size="sm" variant="outline" disabled={!state?.enabled || !canScan || busy || active} onClick={() => void scan()}>
        {busy || active ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}{active ? "扫描中" : "立即扫描"}
      </Button>
    </div>
    <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
      <span>上次：{date(latest?.created_at)}{latest ? ` · ${latest.trigger === "manual_import" ? "单张导入" : latest.trigger === "manual" ? "手动触发" : "定时触发"} · ${labels[latest.status] || latest.status}` : ""}</span>
      {state?.enabled && <span>下次预计：{date(state.next_scan_at)}</span>}
      <button className="underline underline-offset-2" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>{expanded ? "收起记录" : `文件处理记录（${items.size}）`}</button>
    </div>
    {(error || latest?.error_code) && <p role="alert" className="mt-2 text-xs text-red-600">{error || oracleRunErrorMessage(latest?.error_code || "SCAN_UNKNOWN")}</p>}
    {expanded && <div className="mt-3 max-h-80 overflow-auto">
      <table className="w-full text-left text-xs"><thead><tr className="border-b text-muted-foreground"><th className="py-2">PO / 文件</th><th>下载</th><th>处理进度</th><th>结果</th><th>操作</th></tr></thead>
        <tbody>{Array.from(items.values()).map(item => <tr key={item.po_number} className="border-b last:border-0">
          <td className="py-3 pr-3">{item.document_id ? <Link className="underline" href={`/dashboard/documents/${item.document_id}`}>{item.po_number}.pdf</Link> : item.po_number}</td>
          <td className="pr-3">{item.document_id ? "自动下载 ✓" : item.status === "processing" && item.stage === "download" ? "下载中" : "未下载"}</td>
          <td className="pr-3">{labels[item.status === "processing" ? item.stage || item.status : item.status] || item.status}</td>
          <td className="max-w-64 py-2">{item.order_id && <Link className="mr-2 underline" href={`/dashboard/orders/${item.order_id}`}>订单 #{item.order_id}</Link>}{item.inquiry_id && <span>询价 #{item.inquiry_id} </span>}{item.issues?.map(i => oracleIssueMessage(i, item.status)).join("；")}</td>
          <td className="py-2 pl-3">{item.status === "historical_pending" && canScan && <Button size="sm" variant="outline" disabled={!state?.enabled || busy || active} onClick={() => void scan(item.po_number)} aria-label={`导入 ${item.po_number}`}>导入</Button>}</td>
        </tr>)}</tbody>
      </table>{items.size === 0 && <p className="py-4 text-muted-foreground">扫描后将在这里显示每张 PO 的下载及处理结果。</p>}
    </div>}
  </section>;
}
