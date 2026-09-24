"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import {
  AlertTriangle,
  ArrowRightLeft,
  CheckCircle2,
  Clock3,
  Eye,
  Loader2,
  Search,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";

import { DataTable } from "@/components/data-table";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { getUser } from "@/lib/auth";
import {
  listUnitConversionRules,
  retireUnitConversionRule,
  verifyUnitConversionRule,
  type UnitConversionRuleItem,
  type UnitConversionRuleScope,
  type UnitConversionRuleStatus,
} from "@/lib/data-api";
import {
  filterUnitConversionRules,
  summarizeUnitConversionRules,
  unitConversionFormula,
} from "@/lib/unit-conversion-rules-view";

const statusLabels: Record<UnitConversionRuleStatus, string> = {
  draft: "草稿",
  verified: "已审核",
  retired: "已停用",
};

const scopeLabels: Record<UnitConversionRuleScope, string> = {
  source_unit: "相同单位组合",
  product: "指定商品包装",
};

function RuleStatusBadge({ status }: { status: UnitConversionRuleStatus }) {
  const classes = {
    draft: "border-amber-200 bg-amber-50 text-amber-800",
    verified: "border-emerald-200 bg-emerald-50 text-emerald-800",
    retired: "border-slate-200 bg-slate-100 text-slate-600",
  }[status];
  return <Badge className={classes}>{statusLabels[status]}</Badge>;
}

function formatTimestamp(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Tokyo",
  }).format(date);
}

function formatPeriod(rule: UnitConversionRuleItem): string {
  if (!rule.valid_from && !rule.valid_to) return "长期有效";
  return `${rule.valid_from || "不限"} ～ ${rule.valid_to || "不限"}`;
}

function formatPackSignature(value: string | null): string {
  if (!value) return "—";
  try {
    const parsed: unknown = JSON.parse(value);
    if (Array.isArray(parsed)) {
      const parts = parsed.filter((part) => String(part || "").trim());
      if (parts.length) return parts.join(" / ");
    }
  } catch {
    // The raw signature remains visible when an older row is not JSON.
  }
  return value;
}

function actorLabel(id: number | null): string {
  return id == null ? "—" : `用户 #${id}`;
}

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[112px_minmax(0,1fr)] gap-4 border-b border-border/60 py-2.5 text-xs last:border-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words text-foreground">{children}</dd>
    </div>
  );
}

export function RuleDetailsContent({ rule }: { rule: UnitConversionRuleItem }) {
  return (
    <div className="space-y-5">
      {rule.status === "draft" && (
        <div className="flex gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs text-amber-900">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <p className="font-medium">草稿不会自动生效</p>
            <p className="mt-0.5 text-amber-800">必须由管理员根据真实业务证据审核后，系统才会使用这条规则。</p>
          </div>
        </div>
      )}

      <section>
        <h3 className="mb-2 text-xs font-semibold text-foreground">换算关系</h3>
        <dl className="rounded-md border bg-muted/20 px-3">
          <DetailRow label="规则编号">#{rule.id}</DetailRow>
          <DetailRow label="精确关系">
            <span className="font-mono font-medium">{unitConversionFormula(rule)}</span>
          </DetailRow>
          <DetailRow label="来源系统"><span className="font-mono">{rule.source_system}</span></DetailRow>
          <DetailRow label="适用范围">{scopeLabels[rule.scope_type]}</DetailRow>
          <DetailRow label="关联商品">
            {rule.product_id ? (
              <Link className="text-primary underline-offset-2 hover:underline" href={`/dashboard/data?tab=products&product=${rule.product_id}`}>
                商品 #{rule.product_id}
              </Link>
            ) : "所有完全相同的单位组合"}
          </DetailRow>
          <DetailRow label="包装指纹">
            <div>
              <p>{formatPackSignature(rule.pack_signature)}</p>
              {rule.pack_signature && <code className="mt-1 block break-all text-[10px] text-muted-foreground">原始值：{rule.pack_signature}</code>}
            </div>
          </DetailRow>
          <DetailRow label="订购步长">{rule.target_step ?? "未设置"}</DetailRow>
          <DetailRow label="拆包判断">
            {rule.break_pack === true ? "允许拆包" : rule.break_pack === false ? "不允许拆包" : "尚未确认"}
          </DetailRow>
          <DetailRow label="有效期间">{formatPeriod(rule)}</DetailRow>
        </dl>
      </section>

      <section>
        <h3 className="mb-2 text-xs font-semibold text-foreground">业务依据</h3>
        <div className="whitespace-pre-wrap rounded-md border bg-muted/20 p-3 text-xs leading-5">
          {rule.evidence}
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-xs font-semibold text-foreground">当前规则快照</h3>
        <dl className="rounded-md border bg-muted/20 px-3">
          <DetailRow label="当前状态"><RuleStatusBadge status={rule.status} /></DetailRow>
          <DetailRow label="版本">版本 {rule.revision}</DetailRow>
          <DetailRow label="创建人">{actorLabel(rule.created_by)}</DetailRow>
          <DetailRow label="创建时间">{formatTimestamp(rule.created_at)}</DetailRow>
          <DetailRow label="最后更新人">{actorLabel(rule.updated_by)}</DetailRow>
          <DetailRow label="最后更新时间">{formatTimestamp(rule.updated_at)}</DetailRow>
          <DetailRow label="审核人">{actorLabel(rule.verified_by)}</DetailRow>
          <DetailRow label="审核时间">{formatTimestamp(rule.verified_at)}</DetailRow>
        </dl>
        <p className="mt-2 text-[11px] leading-4 text-muted-foreground">
          这里展示数据库当前保存的完整规则快照；现有数据结构不保存每次修改前的历史内容。
        </p>
      </section>
    </div>
  );
}

type RuleAction = "verify" | "retire";

export default function UnitConversionRulesTab() {
  const [rules, setRules] = useState<UnitConversionRuleItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<UnitConversionRuleStatus | "all">("all");
  const [scope, setScope] = useState<UnitConversionRuleScope | "all">("all");
  const [selected, setSelected] = useState<UnitConversionRuleItem | null>(null);
  const [action, setAction] = useState<RuleAction | null>(null);
  const [actionEvidence, setActionEvidence] = useState("");
  const [saving, setSaving] = useState(false);

  const user = getUser();
  const canManage = user?.role === "admin" || user?.role === "superadmin";

  const loadRules = useCallback(async () => {
    setLoadError(null);
    try {
      setRules(await listUnitConversionRules());
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "单位换算规则加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRules();
  }, [loadRules]);

  const summary = useMemo(() => summarizeUnitConversionRules(rules), [rules]);
  const filtered = useMemo(
    () => filterUnitConversionRules(rules, { status, scope, query }),
    [rules, status, scope, query],
  );

  const columns: ColumnDef<UnitConversionRuleItem>[] = [
    {
      accessorKey: "status",
      header: "状态",
      size: 82,
      cell: ({ row }) => <RuleStatusBadge status={row.original.status} />,
    },
    {
      id: "formula",
      header: "换算关系",
      cell: ({ row }) => (
        <div>
          <p className="font-mono font-medium">{unitConversionFormula(row.original)}</p>
          <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">{row.original.source_system}</p>
        </div>
      ),
    },
    {
      accessorKey: "scope_type",
      header: "适用范围",
      size: 150,
      cell: ({ row }) => (
        <div>
          <p>{scopeLabels[row.original.scope_type]}</p>
          <p className="mt-0.5 text-[10px] text-muted-foreground">
            {row.original.product_id ? `商品 #${row.original.product_id}` : "不限商品"}
          </p>
        </div>
      ),
    },
    {
      accessorKey: "evidence",
      header: "依据",
      cell: ({ row }) => (
        <span className="block max-w-[360px] truncate text-muted-foreground" title={row.original.evidence}>
          {row.original.evidence}
        </span>
      ),
    },
    {
      id: "validity",
      header: "有效期间",
      size: 175,
      cell: ({ row }) => <span className="text-muted-foreground">{formatPeriod(row.original)}</span>,
    },
    {
      id: "audit",
      header: "版本 / 更新",
      size: 150,
      cell: ({ row }) => (
        <div className="text-muted-foreground">
          <p>v{row.original.revision} · {actorLabel(row.original.updated_by)}</p>
          <p className="mt-0.5 text-[10px]">{formatTimestamp(row.original.updated_at)}</p>
        </div>
      ),
    },
    {
      id: "actions",
      header: "详情",
      size: 60,
      cell: ({ row }) => (
        <Button
          variant="ghost"
          size="sm"
          className="h-7 gap-1 px-2 text-xs"
          onClick={(event) => {
            event.stopPropagation();
            setSelected(row.original);
          }}
        >
          <Eye className="h-3.5 w-3.5" />查看
        </Button>
      ),
    },
  ];

  function openAction(nextAction: RuleAction) {
    setActionEvidence("");
    setAction(nextAction);
  }

  async function submitAction() {
    if (!selected || !action) return;
    if (!actionEvidence.trim()) {
      toast.error(action === "verify" ? "请填写审核依据" : "请填写停用依据");
      return;
    }
    setSaving(true);
    try {
      const updated = action === "verify"
        ? await verifyUnitConversionRule(selected.id, {
            expected_revision: selected.revision,
            evidence: actionEvidence.trim(),
          })
        : await retireUnitConversionRule(selected.id, {
            expected_revision: selected.revision,
            evidence: actionEvidence.trim(),
          });
      setRules((current) => current.map((rule) => rule.id === updated.id ? updated : rule));
      setSelected(updated);
      setAction(null);
      toast.success(action === "verify" ? "规则已审核并开始生效" : "规则已停用");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "操作失败");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <div className="flex h-64 items-center justify-center"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>;
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="grid shrink-0 grid-cols-2 gap-2 lg:grid-cols-4">
        {[
          { label: "全部规则", value: summary.total, icon: ArrowRightLeft, tone: "text-slate-700" },
          { label: "草稿（未生效）", value: summary.draft, icon: Clock3, tone: "text-amber-700" },
          { label: "已审核（生效）", value: summary.verified, icon: CheckCircle2, tone: "text-emerald-700" },
          { label: "已停用", value: summary.retired, icon: XCircle, tone: "text-slate-500" },
        ].map((item) => (
          <div key={item.label} className="flex items-center justify-between rounded-md border bg-card px-4 py-3 shadow-sm">
            <div>
              <p className="text-[11px] text-muted-foreground">{item.label}</p>
              <p className="mt-1 text-xl font-semibold tabular-nums">{item.value}</p>
            </div>
            <item.icon className={`h-5 w-5 ${item.tone}`} />
          </div>
        ))}
      </div>

      <div className="flex shrink-0 items-start gap-2 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-900">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
        <p><strong>规则透明原则：</strong>只有“已审核”规则会自动参与换算；草稿只供核对，停用规则仅保留记录。新规则必须从具体 PO 商品行登记。</p>
      </div>

      {loadError ? (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 p-5 text-center text-sm text-red-800">
          <p>加载失败：{loadError}</p>
          <Button variant="outline" size="sm" className="mt-3" onClick={() => { setLoading(true); void loadRules(); }}>重新加载</Button>
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-hidden rounded-md border bg-card">
          <DataTable
            columns={columns}
            data={filtered}
            pageSize={20}
            onRowClick={setSelected}
            toolbar={(
              <div className="flex flex-1 flex-wrap items-center gap-2">
                <div className="relative min-w-[220px] flex-1 lg:max-w-sm">
                  <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                  <Input className="h-8 pl-9 text-xs" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索单位、商品编号或依据" />
                </div>
                <Select value={status} onValueChange={(value) => setStatus(value as UnitConversionRuleStatus | "all")}>
                  <SelectTrigger size="sm" className="w-[145px]"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部状态</SelectItem>
                    <SelectItem value="draft">草稿（未生效）</SelectItem>
                    <SelectItem value="verified">已审核（生效）</SelectItem>
                    <SelectItem value="retired">已停用</SelectItem>
                  </SelectContent>
                </Select>
                <Select value={scope} onValueChange={(value) => setScope(value as UnitConversionRuleScope | "all")}>
                  <SelectTrigger size="sm" className="w-[145px]"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部适用范围</SelectItem>
                    <SelectItem value="source_unit">相同单位组合</SelectItem>
                    <SelectItem value="product">指定商品包装</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}
            emptyState={<EmptyState icon={ArrowRightLeft} title="没有符合条件的换算规则" description="可以调整筛选条件；新规则需要从具体 PO 商品行登记。" />}
          />
        </div>
      )}

      <Sheet open={selected !== null} onOpenChange={(open) => { if (!open) setSelected(null); }}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
          {selected && (
            <>
              <SheetHeader className="border-b">
                <div className="flex items-center gap-2 pr-8">
                  <SheetTitle className="text-base">单位换算规则 #{selected.id}</SheetTitle>
                  <RuleStatusBadge status={selected.status} />
                </div>
                <SheetDescription>数据库当前规则及其业务依据</SheetDescription>
              </SheetHeader>
              <div className="flex-1 px-4 pb-4"><RuleDetailsContent rule={selected} /></div>
              {canManage && selected.status !== "retired" && (
                <SheetFooter className="sticky bottom-0 flex-row justify-end border-t bg-background">
                  <Button variant="outline" onClick={() => openAction("retire")}>停用规则</Button>
                  {selected.status === "draft" && <Button onClick={() => openAction("verify")}>审核并启用</Button>}
                </SheetFooter>
              )}
            </>
          )}
        </SheetContent>
      </Sheet>

      <Dialog open={action !== null} onOpenChange={(open) => { if (!open && !saving) setAction(null); }}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>{action === "verify" ? "审核并启用规则" : "停用规则"}</DialogTitle></DialogHeader>
          {selected && (
            <div className="space-y-4 py-2">
              <div className="rounded-md border bg-muted/30 p-3 text-xs">
                <p className="font-mono font-medium">{unitConversionFormula(selected)}</p>
                <p className="mt-1 text-muted-foreground">当前版本 v{selected.revision} · {scopeLabels[selected.scope_type]}</p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="rule-action-evidence">{action === "verify" ? "审核依据" : "停用依据"} *</Label>
                <Textarea id="rule-action-evidence" rows={4} value={actionEvidence} onChange={(event) => setActionEvidence(event.target.value)} placeholder={action === "verify" ? "填写可复核的业务证据，例如供应商书面确认" : "填写停用原因，例如供应商包装规格已变更"} />
              </div>
              {action === "verify" && <p className="text-xs text-amber-700">确认后，系统会在适用范围和有效期间内自动使用此规则。</p>}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" disabled={saving} onClick={() => setAction(null)}>取消</Button>
            <Button variant={action === "retire" ? "destructive" : "default"} disabled={saving || !actionEvidence.trim()} onClick={() => void submitAction()}>
              {saving && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
              {action === "verify" ? "确认审核并启用" : "确认停用"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
