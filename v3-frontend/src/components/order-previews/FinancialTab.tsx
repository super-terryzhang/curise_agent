"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowDownUp,
  ChevronDown,
  Download,
  Loader2,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

import {
  createOrderCostItem,
  deleteOrderCostItem,
  downloadOrderFinancialsXlsx,
  getOrderFinancials,
  type OrderCostItem,
  type OrderFinancials,
  type OrderProductPnlLine,
  updateOrderCostItem,
  updateOrderFinancialSettings,
} from "@/lib/orders-api";

interface FinancialTabProps {
  orderId: number;
}

const COMMON_CURRENCIES = ["JPY", "USD", "CNY", "AUD", "EUR", "GBP", "KRW", "THB", "SGD", "NZD"];

const COST_CATEGORY_PRESETS = [
  "运费",
  "人工费",
  "关税",
  "保险费",
  "仓储费",
  "包装费",
  "其他",
];

function fmt(n: number, decimals = 2): string {
  return n.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function profitColor(n: number): string {
  return n >= 0 ? "text-emerald-500" : "text-destructive";
}

function marginColor(m: number): string {
  if (m > 10) return "text-emerald-500";
  if (m >= 0) return "text-amber-500";
  return "text-destructive";
}

export default function FinancialTab({ orderId }: FinancialTabProps) {
  const [data, setData] = useState<OrderFinancials | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const fresh = await getOrderFinancials(orderId);
      setData(fresh);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [orderId]);

  useEffect(() => {
    setLoading(true);
    reload();
  }, [reload]);

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="h-full flex items-center justify-center text-xs text-destructive">
        {error || "无数据"}
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <SettingsBar
        data={data}
        orderId={orderId}
        onUpdated={reload}
      />

      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {data.warnings.length > 0 && (
          <Card className="bg-amber-500/5 border-amber-500/20">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-medium text-amber-600 dark:text-amber-400 flex items-center gap-2">
                <AlertTriangle className="h-3.5 w-3.5" />
                警告 ({data.warnings.length})
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-1 pb-3 space-y-1">
              {data.warnings.map((w, i) => (
                <div key={i} className="text-[11px] text-amber-700 dark:text-amber-300">
                  {w}
                </div>
              ))}
            </CardContent>
          </Card>
        )}

        <EquationStrip data={data} />

        <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
          <div className="lg:col-span-3">
            <CostItemsTable data={data} orderId={orderId} onChanged={reload} />
          </div>
          <div className="lg:col-span-2">
            <ProfitBreakdownBar data={data} />
          </div>
        </div>

        <ProductMarginTable lines={data.product_lines} currency={data.display_currency} />
      </div>
    </div>
  );
}

// ─── Settings bar (top) ──────────────────────────────────────

function SettingsBar({
  data,
  orderId,
  onUpdated,
}: {
  data: OrderFinancials;
  orderId: number;
  onUpdated: () => Promise<void>;
}) {
  const [taxRateInput, setTaxRateInput] = useState(
    (data.tax_rate * 100).toFixed(2),
  );
  const [savingTax, setSavingTax] = useState(false);
  const [savingCcy, setSavingCcy] = useState(false);
  const [exporting, setExporting] = useState(false);

  // Re-sync input when server updates the rate.
  useEffect(() => {
    setTaxRateInput((data.tax_rate * 100).toFixed(2));
  }, [data.tax_rate]);

  async function commitTaxRate() {
    const pct = parseFloat(taxRateInput);
    if (isNaN(pct) || pct < 0 || pct > 100) {
      toast.error("税率需在 0–100 之间");
      setTaxRateInput((data.tax_rate * 100).toFixed(2));
      return;
    }
    const newRate = pct / 100;
    if (Math.abs(newRate - data.tax_rate) < 1e-6) return;
    setSavingTax(true);
    try {
      await updateOrderFinancialSettings(orderId, { tax_rate: newRate });
      await onUpdated();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
      setTaxRateInput((data.tax_rate * 100).toFixed(2));
    } finally {
      setSavingTax(false);
    }
  }

  async function setCurrency(next: string) {
    if (next === data.display_currency) return;
    setSavingCcy(true);
    try {
      await updateOrderFinancialSettings(orderId, { display_currency: next });
      await onUpdated();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "切换币种失败");
    } finally {
      setSavingCcy(false);
    }
  }

  async function onExport() {
    setExporting(true);
    try {
      await downloadOrderFinancialsXlsx(orderId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "下载失败");
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="shrink-0 px-6 py-3 border-b border-border/50 flex items-center justify-between gap-3 flex-wrap">
      <div className="flex items-center gap-4 text-xs">
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">显示币种</span>
          <Select
            value={data.display_currency}
            onValueChange={setCurrency}
            disabled={savingCcy}
          >
            <SelectTrigger className="h-7 w-[90px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {COMMON_CURRENCIES.map((c) => (
                <SelectItem key={c} value={c} className="text-xs">
                  {c}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {savingCcy && <RefreshCw className="h-3 w-3 animate-spin text-muted-foreground" />}
        </div>

        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">税率</span>
          <div className="flex items-center gap-1">
            <Input
              value={taxRateInput}
              onChange={(e) => setTaxRateInput(e.target.value)}
              onBlur={commitTaxRate}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  (e.target as HTMLInputElement).blur();
                }
              }}
              className="h-7 w-[60px] text-xs text-right"
              disabled={savingTax}
            />
            <span className="text-muted-foreground">%</span>
            {savingTax && <RefreshCw className="h-3 w-3 animate-spin text-muted-foreground" />}
          </div>
        </div>

        <div className="text-[11px] text-muted-foreground">
          订单币种 {data.order_currency}
        </div>
      </div>

      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          className="h-8 text-xs"
          onClick={() => {
            // Open the AI sidebar and ask it to analyze this order.
            // Provider+Sidebar both listen for this event (see
            // AssistantProvider.tsx and AssistantSidebar.tsx). The
            // pageContext (URL) is read inside the provider, so the
            // agent's page-context auto-injection picks up order_id=N.
            window.dispatchEvent(
              new CustomEvent("assistant:openAndSend", {
                detail: { text: "分析这个订单" },
              }),
            );
          }}
          title="让 AI 助手生成本订单的财务分析简报"
        >
          <Sparkles className="mr-1 h-3 w-3" />
          智能分析
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-8 text-xs"
          onClick={onExport}
          disabled={exporting}
        >
          {exporting ? (
            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
          ) : (
            <Download className="mr-1 h-3 w-3" />
          )}
          导出 Excel
        </Button>
      </div>
    </div>
  );
}

// ─── Equation strip ──────────────────────────────────────────

function EquationStrip({ data }: { data: OrderFinancials }) {
  const s = data.summary;
  const ccy = data.display_currency;
  return (
    <Card>
      <CardContent className="py-4">
        <div className="flex items-center gap-3 flex-wrap text-sm">
          <Term label="产品总金额" value={s.product_revenue} ccy={ccy} />
          <Op>−</Op>
          <Term label="总成本" value={s.total_cost} ccy={ccy} muted />
          <Op>=</Op>
          <Term
            label="毛利润"
            value={s.gross_profit}
            ccy={ccy}
            className={profitColor(s.gross_profit)}
            sub={`毛利率 ${fmt(s.gross_margin, 2)}%`}
          />
          <Op>−</Op>
          <Term
            label="税费"
            value={s.tax_amount}
            ccy={ccy}
            muted
            sub={`@${fmt(data.tax_rate * 100, 2)}%`}
          />
          <Op>=</Op>
          <Term
            label="净利润"
            value={s.net_profit}
            ccy={ccy}
            className={cn("font-semibold", profitColor(s.net_profit))}
            sub={`净利率 ${fmt(s.net_margin, 2)}%`}
          />
        </div>

        <div className="mt-3 pt-3 border-t border-border/40 grid grid-cols-2 sm:grid-cols-4 gap-3 text-[11px] text-muted-foreground">
          <div>
            产品成本 <span className="text-foreground">{fmt(s.product_cost)} {ccy}</span>
          </div>
          <div>
            其他费用合计 <span className="text-foreground">{fmt(s.extra_costs_total)} {ccy}</span>
          </div>
          <div>
            产品行 <span className="text-foreground">{data.product_lines.length}</span>
          </div>
          <div>
            费用项 <span className="text-foreground">{data.cost_items.length}</span>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function Term({
  label,
  value,
  ccy,
  className,
  muted,
  sub,
}: {
  label: string;
  value: number;
  ccy: string;
  className?: string;
  muted?: boolean;
  sub?: string;
}) {
  return (
    <div className={cn("flex flex-col", muted && "text-muted-foreground")}>
      <span className={cn("font-mono tabular-nums", className)}>{fmt(value)}</span>
      <span className="text-[10px] text-muted-foreground">
        {label} ({ccy}){sub ? ` · ${sub}` : ""}
      </span>
    </div>
  );
}

function Op({ children }: { children: React.ReactNode }) {
  return <span className="text-xl text-muted-foreground font-light">{children}</span>;
}

// ─── Cost items table (editable, debounced auto-save) ────────

interface DraftRow {
  category: string;
  amount: string;
  currency: string;
  notes: string;
}

function emptyDraft(): DraftRow {
  return { category: "运费", amount: "", currency: "JPY", notes: "" };
}

function CostItemsTable({
  data,
  orderId,
  onChanged,
}: {
  data: OrderFinancials;
  orderId: number;
  onChanged: () => Promise<void>;
}) {
  const [draft, setDraft] = useState<DraftRow>(emptyDraft);
  const [adding, setAdding] = useState(false);

  async function onAdd() {
    const amount = parseFloat(draft.amount);
    if (!draft.category.trim() || isNaN(amount)) {
      toast.error("请填写类别和金额");
      return;
    }
    setAdding(true);
    try {
      await createOrderCostItem(orderId, {
        category: draft.category.trim(),
        amount,
        currency: draft.currency,
        notes: draft.notes.trim() || null,
      });
      setDraft(emptyDraft());
      await onChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "添加失败");
    } finally {
      setAdding(false);
    }
  }

  return (
    <Card>
      <CardHeader className="pb-2 flex flex-row items-center justify-between">
        <CardTitle className="text-xs font-medium">额外费用</CardTitle>
        <span className="text-[10px] text-muted-foreground">
          合计 {fmt(data.summary.extra_costs_total)} {data.display_currency}
        </span>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border/50 text-muted-foreground">
                <th className="px-3 py-2 text-left font-medium">类别</th>
                <th className="px-3 py-2 text-right font-medium">金额</th>
                <th className="px-3 py-2 text-left font-medium">币种</th>
                <th className="px-3 py-2 text-right font-medium">
                  折算 ({data.display_currency})
                </th>
                <th className="px-3 py-2 text-left font-medium">备注</th>
                <th className="w-8" />
              </tr>
            </thead>
            <tbody>
              {data.cost_items.map((item) => (
                <EditableCostRow
                  key={item.id ?? `tmp-${item.category}`}
                  item={item}
                  orderId={orderId}
                  onChanged={onChanged}
                />
              ))}
              {data.cost_items.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-3 py-4 text-center text-muted-foreground">
                    暂无额外费用。在下方添加运费、人工费、关税等。
                  </td>
                </tr>
              )}
              {/* New-row composer */}
              <tr className="border-t border-border/50 bg-muted/20">
                <td className="px-3 py-2">
                  <Input
                    list="cost-category-presets"
                    value={draft.category}
                    onChange={(e) => setDraft((d) => ({ ...d, category: e.target.value }))}
                    placeholder="类别"
                    className="h-7 text-xs"
                  />
                  <datalist id="cost-category-presets">
                    {COST_CATEGORY_PRESETS.map((c) => (
                      <option key={c} value={c} />
                    ))}
                  </datalist>
                </td>
                <td className="px-3 py-2">
                  <Input
                    value={draft.amount}
                    onChange={(e) => setDraft((d) => ({ ...d, amount: e.target.value }))}
                    placeholder="0"
                    className="h-7 text-xs text-right"
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        onAdd();
                      }
                    }}
                  />
                </td>
                <td className="px-3 py-2">
                  <Select
                    value={draft.currency}
                    onValueChange={(v) => setDraft((d) => ({ ...d, currency: v }))}
                  >
                    <SelectTrigger className="h-7 text-xs w-[80px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {COMMON_CURRENCIES.map((c) => (
                        <SelectItem key={c} value={c} className="text-xs">
                          {c}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </td>
                <td className="px-3 py-2 text-right text-muted-foreground">—</td>
                <td className="px-3 py-2">
                  <Input
                    value={draft.notes}
                    onChange={(e) => setDraft((d) => ({ ...d, notes: e.target.value }))}
                    placeholder="备注（可选）"
                    className="h-7 text-xs"
                  />
                </td>
                <td className="px-2 py-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 w-7 p-0"
                    onClick={onAdd}
                    disabled={adding}
                    title="添加"
                  >
                    {adding ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Plus className="h-3.5 w-3.5" />
                    )}
                  </Button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function EditableCostRow({
  item,
  orderId,
  onChanged,
}: {
  item: OrderCostItem;
  orderId: number;
  onChanged: () => Promise<void>;
}) {
  // Drafts shadow the server value; changes flush after 800ms of inactivity.
  const [draft, setDraft] = useState({
    category: item.category,
    amount: String(item.amount_original),
    currency: item.currency_original,
    notes: item.notes ?? "",
  });
  const [deleting, setDeleting] = useState(false);

  // When the server pushes a new value (other tab, agent, etc.) re-sync.
  useEffect(() => {
    setDraft({
      category: item.category,
      amount: String(item.amount_original),
      currency: item.currency_original,
      notes: item.notes ?? "",
    });
  }, [item.id, item.category, item.amount_original, item.currency_original, item.notes]);

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingRef = useRef<Partial<{
    category: string;
    amount: number;
    currency: string;
    notes: string | null;
  }>>({});

  const flush = useCallback(async () => {
    if (item.id == null) return;
    const payload = pendingRef.current;
    if (Object.keys(payload).length === 0) return;
    try {
      await updateOrderCostItem(orderId, item.id, payload);
      pendingRef.current = {};
      await onChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    }
  }, [item.id, orderId, onChanged]);

  const schedule = useCallback(
    (patch: Partial<{
      category: string;
      amount: number;
      currency: string;
      notes: string | null;
    }>) => {
      pendingRef.current = { ...pendingRef.current, ...patch };
      if (timerRef.current) clearTimeout(timerRef.current);
      // 800ms debounce — matches SupplierLetterheadSection.
      timerRef.current = setTimeout(flush, 800);
    },
    [flush],
  );

  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current);
  }, []);

  async function onDelete() {
    if (item.id == null) return;
    if (!confirm(`删除费用「${item.category}」？`)) return;
    setDeleting(true);
    try {
      await deleteOrderCostItem(orderId, item.id);
      await onChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <tr className={cn("border-b border-border/30 hover:bg-muted/30", !item.fx_ok && "bg-amber-500/5")}>
      <td className="px-3 py-1.5">
        <Input
          list="cost-category-presets"
          value={draft.category}
          onChange={(e) => {
            const v = e.target.value;
            setDraft((d) => ({ ...d, category: v }));
            if (v.trim()) schedule({ category: v.trim() });
          }}
          className="h-7 text-xs border-transparent hover:border-input focus:border-input bg-transparent"
        />
      </td>
      <td className="px-3 py-1.5">
        <Input
          value={draft.amount}
          onChange={(e) => {
            const v = e.target.value;
            setDraft((d) => ({ ...d, amount: v }));
            const n = parseFloat(v);
            if (!isNaN(n)) schedule({ amount: n });
          }}
          className="h-7 text-xs text-right border-transparent hover:border-input focus:border-input bg-transparent"
        />
      </td>
      <td className="px-3 py-1.5">
        <Select
          value={draft.currency}
          onValueChange={(v) => {
            setDraft((d) => ({ ...d, currency: v }));
            schedule({ currency: v });
          }}
        >
          <SelectTrigger className="h-7 text-xs w-[80px] border-transparent hover:border-input">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {COMMON_CURRENCIES.map((c) => (
              <SelectItem key={c} value={c} className="text-xs">
                {c}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </td>
      <td className="px-3 py-1.5 text-right font-mono tabular-nums">
        {item.fx_ok ? (
          fmt(item.amount_display)
        ) : (
          <span className="text-amber-500" title="汇率缺失">⚠ 缺汇率</span>
        )}
      </td>
      <td className="px-3 py-1.5">
        <Input
          value={draft.notes}
          onChange={(e) => {
            const v = e.target.value;
            setDraft((d) => ({ ...d, notes: v }));
            schedule({ notes: v.trim() ? v.trim() : null });
          }}
          placeholder="—"
          className="h-7 text-xs border-transparent hover:border-input focus:border-input bg-transparent"
        />
      </td>
      <td className="px-2 py-1.5">
        <Button
          size="sm"
          variant="ghost"
          className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
          onClick={onDelete}
          disabled={deleting}
          title="删除"
        >
          {deleting ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Trash2 className="h-3.5 w-3.5" />
          )}
        </Button>
      </td>
    </tr>
  );
}

// ─── Profit breakdown bar ────────────────────────────────────

function ProfitBreakdownBar({ data }: { data: OrderFinancials }) {
  const s = data.summary;
  const total = Math.max(s.product_revenue, 1);
  const productCostPct = (s.product_cost / total) * 100;
  const extraCostPct = (s.extra_costs_total / total) * 100;
  const taxPct = (s.tax_amount / total) * 100;
  const netPct = (s.net_profit / total) * 100;

  return (
    <Card className="h-full">
      <CardHeader className="pb-2">
        <CardTitle className="text-xs font-medium">收入构成</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="h-3 w-full rounded-full overflow-hidden flex bg-muted">
          <Slice color="bg-slate-400" widthPct={productCostPct} title="产品成本" />
          <Slice color="bg-orange-400" widthPct={extraCostPct} title="额外费用" />
          <Slice color="bg-rose-400" widthPct={Math.max(taxPct, 0)} title="税费" />
          <Slice
            color={s.net_profit >= 0 ? "bg-emerald-500" : "bg-destructive"}
            widthPct={Math.max(netPct, 0)}
            title="净利润"
          />
        </div>

        <div className="space-y-1.5 text-[11px]">
          <LegendRow color="bg-slate-400" label="产品成本" value={s.product_cost} pct={productCostPct} ccy={data.display_currency} />
          <LegendRow color="bg-orange-400" label="额外费用" value={s.extra_costs_total} pct={extraCostPct} ccy={data.display_currency} />
          <LegendRow color="bg-rose-400" label="税费" value={s.tax_amount} pct={taxPct} ccy={data.display_currency} />
          <LegendRow
            color={s.net_profit >= 0 ? "bg-emerald-500" : "bg-destructive"}
            label="净利润"
            value={s.net_profit}
            pct={netPct}
            ccy={data.display_currency}
            highlight
          />
        </div>

        {s.product_revenue === 0 && (
          <div className="text-[11px] text-muted-foreground">
            订单收入为 0，无法计算占比。
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Slice({ color, widthPct, title }: { color: string; widthPct: number; title: string }) {
  if (widthPct <= 0) return null;
  return (
    <div
      className={color}
      style={{ width: `${Math.min(widthPct, 100)}%` }}
      title={`${title} ${widthPct.toFixed(1)}%`}
    />
  );
}

function LegendRow({
  color,
  label,
  value,
  pct,
  ccy,
  highlight,
}: {
  color: string;
  label: string;
  value: number;
  pct: number;
  ccy: string;
  highlight?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <div className="flex items-center gap-1.5">
        <span className={cn("inline-block h-2 w-2 rounded-sm", color)} />
        <span className={cn(highlight && "font-medium")}>{label}</span>
      </div>
      <div className="flex items-center gap-2 font-mono tabular-nums">
        <span className={cn(highlight && "font-medium", value < 0 && "text-destructive")}>
          {fmt(value)} {ccy}
        </span>
        <span className="text-muted-foreground w-12 text-right">{pct.toFixed(1)}%</span>
      </div>
    </div>
  );
}

// ─── Product margin table ────────────────────────────────────

type SortKey = "product_name" | "quantity" | "unit_price" | "revenue" | "cost" | "profit" | "margin";
type SortDir = "asc" | "desc";

function ProductMarginTable({
  lines,
  currency,
}: {
  lines: OrderProductPnlLine[];
  currency: string;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("margin");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const sorted = useMemo(() => {
    const copy = [...lines];
    copy.sort((a, b) => {
      const av = a[sortKey] as number | string;
      const bv = b[sortKey] as number | string;
      if (typeof av === "string" && typeof bv === "string") {
        return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
      }
      const n = (av as number) - (bv as number);
      return sortDir === "asc" ? n : -n;
    });
    return copy;
  }, [lines, sortKey, sortDir]);

  function toggle(k: SortKey) {
    if (k === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(k);
      setSortDir("desc");
    }
  }

  if (lines.length === 0) {
    return (
      <Card>
        <CardContent className="py-6 text-center text-xs text-muted-foreground">
          订单暂无产品行。
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-xs font-medium">产品利润明细</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border/50 text-muted-foreground">
                <SortableTh label="产品" k="product_name" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} align="left" />
                <SortableTh label="数量" k="quantity" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} />
                <SortableTh label={`PO 单价 (${currency})`} k="unit_price" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} />
                {/* R2 — 卖价 + PO 偏差。无卖价的行该列显示 —。
                    Renamed 2026-06-22: "合同卖价" → "卖价" (DB column still
                    `contract_price` per migration 0014). */}
                <th className="px-3 py-2 text-right text-[11px] font-normal">卖价 ({currency})</th>
                <th className="px-3 py-2 text-right text-[11px] font-normal">PO 偏差</th>
                <SortableTh label={`收入 (${currency})`} k="revenue" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} />
                <SortableTh label={`成本 (${currency})`} k="cost" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} />
                <SortableTh label={`利润 (${currency})`} k="profit" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} />
                <SortableTh label="利润率" k="margin" sortKey={sortKey} sortDir={sortDir} onToggle={toggle} />
              </tr>
            </thead>
            <tbody>
              {sorted.map((p, i) => {
                // R2 — PO vs contract delta. >5% absolute = anomaly highlight,
                // matches the "皇家订单会把金额搞错" failure mode Felix
                // described (typos in customer POs).
                const hasContract = p.contract_price != null;
                const deltaPct = hasContract && (p.contract_price as number) > 0
                  ? ((p.unit_price - (p.contract_price as number)) /
                      (p.contract_price as number)) *
                    100
                  : null;
                const deltaClass =
                  deltaPct == null
                    ? "text-muted-foreground"
                    : Math.abs(deltaPct) >= 5
                      ? "text-destructive font-semibold"
                      : Math.abs(deltaPct) >= 1
                        ? "text-amber-600"
                        : "text-muted-foreground";
                return (
                  <tr key={i} className="border-b border-border/30 hover:bg-muted/30">
                    <td className="px-3 py-2">
                      <div className="font-medium truncate max-w-[260px]" title={p.product_name}>
                        {p.product_name}
                      </div>
                      {p.product_code && (
                        <div className="text-[10px] text-muted-foreground">{p.product_code}</div>
                      )}
                      {!p.matched && (
                        <div className="text-[10px] text-amber-600">未匹配 · 成本计为 0</div>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmt(p.quantity, 0)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmt(p.unit_price)}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                      {hasContract ? fmt(p.contract_price as number) : "—"}
                    </td>
                    <td className={cn("px-3 py-2 text-right tabular-nums", deltaClass)}>
                      {deltaPct == null
                        ? "—"
                        : `${deltaPct > 0 ? "+" : ""}${deltaPct.toFixed(1)}%`}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmt(p.revenue)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmt(p.cost)}</td>
                    <td className={cn("px-3 py-2 text-right tabular-nums font-medium", profitColor(p.profit))}>
                      {fmt(p.profit)}
                    </td>
                    <td className={cn("px-3 py-2 text-right tabular-nums font-medium", marginColor(p.margin))}>
                      {fmt(p.margin, 2)}%
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function SortableTh({
  label,
  k,
  sortKey,
  sortDir,
  onToggle,
  align = "right",
}: {
  label: string;
  k: SortKey;
  sortKey: SortKey;
  sortDir: SortDir;
  onToggle: (k: SortKey) => void;
  align?: "left" | "right";
}) {
  const active = sortKey === k;
  return (
    <th
      className={cn(
        "px-3 py-2 font-medium cursor-pointer select-none hover:text-foreground",
        align === "left" ? "text-left" : "text-right",
      )}
      onClick={() => onToggle(k)}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active ? (
          <ChevronDown
            className={cn("h-3 w-3 transition-transform", sortDir === "asc" && "rotate-180")}
          />
        ) : (
          <ArrowDownUp className="h-2.5 w-2.5 opacity-40" />
        )}
      </span>
    </th>
  );
}
