"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { getProductPriceHistory, restoreProductPrice, type ProductItem,
  type ProductPriceHistory, type ProductPriceEvent, type ProductPriceSnapshot } from "@/lib/data-api";

const EVENT_LABELS = { initial: "初始价格", baseline: "迁移价格基线", change: "价格调整",
  restore: "恢复价格", delete: "产品删除", legacy: "旧记录片段" };
const FIELDS: Record<string, string> = { price: "采购价", contract_price: "卖价", currency: "币种",
  purchase_price_effective_from: "采购价有效开始", purchase_price_effective_to: "采购价有效结束",
  selling_price_effective_from: "卖价有效开始", selling_price_effective_to: "卖价有效结束",
  unit: "单位", unit_size: "规格", pack_size: "包装", supplier_id: "供应商", country_id: "国家", port_id: "港口" };
const SOURCES: Record<string, string> = { http: "产品页面", ai: "AI 确认", upload: "Excel 导入",
  legacy_v2: "旧版导入记录", legacy_v3: "旧版字段日志", rollback: "批次回滚", migration: "迁移基线", internal: "系统" };

export function priceText(snapshot: ProductPriceSnapshot | null, field: "price" | "contract_price") {
  if (!snapshot) return "—";
  if (snapshot.known_fields && !snapshot.known_fields.includes(field)) return "未记录";
  const value = snapshot[field];
  if (value === null || value === undefined) return "未设置";
  return `${value} ${snapshot.currency || "币种未记录"}${snapshot.unit ? ` / ${snapshot.unit}` : ""}`;
}

function periodText(
  snapshot: ProductPriceSnapshot,
  startField: "purchase_price_effective_from" | "selling_price_effective_from",
  endField: "purchase_price_effective_to" | "selling_price_effective_to",
) {
  const start = snapshot[startField]?.slice(0, 10) || "不限";
  const end = snapshot[endField]?.slice(0, 10) || "不限";
  return `${start} 至 ${end}`;
}

export function ProductPriceHistoryDialog({ product, canRestore, onClose, onRestored }: {
  product: ProductItem; canRestore: boolean; onClose: () => void; onRestored: () => void;
}) {
  const [data, setData] = useState<ProductPriceHistory | null>(null);
  const [page, setPage] = useState(0);
  const [legacy, setLegacy] = useState(false);
  const [field, setField] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [restoring, setRestoring] = useState(false);
  const [confirmEvent, setConfirmEvent] = useState<ProductPriceEvent | null>(null);
  const ceiling = useRef<number | undefined>(undefined);
  const PAGE_SIZE = 10;

  function resetPages() { ceiling.current = undefined; setPage(0); setConfirmEvent(null); }
  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError("");
    getProductPriceHistory(product.id, { limit: PAGE_SIZE, offset: page * PAGE_SIZE,
      max_version: ceiling.current, field: field || undefined, legacy,
      date_from: from ? new Date(`${from}T00:00:00`).toISOString() : undefined,
      date_to: to ? new Date(`${to}T23:59:59.999`).toISOString() : undefined,
    }).then(result => {
      if (!cancelled) { setData(result); ceiling.current = result.max_version ?? undefined; }
    }).catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : "历史加载失败"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [product.id, page, field, from, to, refresh, legacy]);

  async function restore() {
    if (!confirmEvent || data?.revision == null) return;
    setRestoring(true);
    try {
      await restoreProductPrice(product.id, confirmEvent.id, data.revision);
      toast.success("价格已恢复，并保留本次恢复记录");
      resetPages(); setRefresh(v => v + 1); onRestored();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "恢复失败，请刷新后核对价格");
    } finally { setRestoring(false); }
  }

  return <Dialog open onOpenChange={open => { if (!open && !restoring) onClose(); }}>
    <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
      <DialogHeader><DialogTitle>{product.product_name_en || `产品 #${product.id}`} · 价格历史</DialogTitle><DialogDescription>查看这个产品的采购价、卖价及计价条件变化。</DialogDescription></DialogHeader>
      {data?.current && <div className="rounded-lg bg-muted p-3 text-sm space-y-1">
        <p>当前采购价：{priceText(data.current, "price")}</p>
        <p>采购价有效期：{periodText(data.current, "purchase_price_effective_from", "purchase_price_effective_to")}</p>
        <p>当前卖价：{priceText(data.current, "contract_price")}</p>
        <p>卖价有效期：{periodText(data.current, "selling_price_effective_from", "selling_price_effective_to")}</p>
      </div>}
      <div className="flex flex-wrap gap-3 text-sm items-end">
        <label>记录范围<select aria-label="记录范围" value={legacy ? "legacy" : "complete"} onChange={e => { resetPages(); setLegacy(e.target.value === "legacy"); setFrom(""); setTo(""); }} className="block border rounded p-2 bg-background">
          <option value="complete">完整记录</option><option value="legacy">旧记录片段</option>
        </select></label>
        <label>价格类型<select aria-label="筛选价格类型" value={field} onChange={e => { resetPages(); setField(e.target.value); }} className="block border rounded p-2 bg-background">
          <option value="">全部</option><option value="price">采购价</option><option value="contract_price">卖价</option>
        </select></label>
        <label>开始日期<input aria-label="开始日期" type="date" disabled={legacy} value={from} onChange={e => { resetPages(); setFrom(e.target.value); }} className="block border rounded p-2" /></label>
        <label>结束日期<input aria-label="结束日期" type="date" disabled={legacy} value={to} onChange={e => { resetPages(); setTo(e.target.value); }} className="block border rounded p-2" /></label>
        <Button variant="outline" onClick={() => { resetPages(); setRefresh(v => v + 1); }}>刷新</Button>
      </div>
      {loading ? <p role="status">正在加载价格历史…</p> : error ? <p role="alert" className="text-destructive">{error}，请刷新重试。</p> : data && <>
        <p className="text-sm text-muted-foreground">{legacy ? `${data.total} 条旧记录片段（无法由此确定完整变更次数）` : `${data.change_count} 次价格或计价条件变更 · ${data.total} 条记录（初始价格和迁移基线不计入变更次数）`}</p>
        {!legacy && data.legacy_count > 0 && <p className="text-sm text-muted-foreground">另保留 {data.legacy_count} 条不完整旧记录，不计入上述完整变更次数。</p>}
        {data.items.length === 0 && <p>此筛选范围内没有价格记录。</p>}
        {data.items.map(event => <article key={event.id} className="border rounded-lg p-4 space-y-2">
          <div className="flex flex-wrap justify-between gap-2 text-sm">
            <strong>{EVENT_LABELS[event.event_type]}{event.version != null && event.version > 0 ? ` · 版本 ${event.version}` : ""}</strong>
            <time dateTime={event.recorded_at}>{event.event_type === "legacy" ? `原日志时间：${event.after?.legacy_recorded_at || "未记录"}（时区未注明）` : new Date(event.recorded_at).toLocaleString()}</time>
          </div>
          {event.event_type === "legacy" && <p className="text-xs text-muted-foreground">仅证明这个字段曾被记录；其他价格、币种和单位可能未知，不能直接恢复，也不代表完整调价次数。</p>}
          {event.event_type === "baseline" && <p className="text-xs text-muted-foreground">从此时开始完整记录；这不是产品最初定价的日期。</p>}
          <table className="w-full text-sm text-left"><thead><tr><th>字段</th><th>修改前</th><th>修改后</th></tr></thead>
            <tbody>{(["price", "contract_price"] as const).map(key => <tr key={key} className={event.changed_fields.includes(key) ? "font-medium" : "text-muted-foreground"}>
              <td className="py-1">{FIELDS[key]}</td><td>{priceText(event.before, key)}</td><td>{priceText(event.after, key)}</td>
            </tr>)}</tbody></table>
          {event.changed_fields.filter(key => !["price", "contract_price"].includes(key)).map(key => <p key={key} className="text-xs">
            {FIELDS[key] || key}：{String((event.before as unknown as Record<string, unknown> | null)?.[key] ?? "未设置")} → {String((event.after as unknown as Record<string, unknown> | null)?.[key] ?? "未设置")}
          </p>)}
          <div className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
            <span>{SOURCES[event.source] || event.source}{event.source_batch_id ? ` · 批次 #${event.source_batch_id}` : ""} · {event.actor_id == null ? "系统记录" : `操作人 #${event.actor_id}`}</span>
            {canRestore && !data.deleted && event.after && event.version !== data.max_version && event.event_type !== "legacy" && <Button size="sm" variant="outline" disabled={restoring} onClick={() => setConfirmEvent(event)}>恢复为此价格</Button>}
          </div>
        </article>)}
        <div className="flex justify-between items-center"><Button variant="outline" disabled={page === 0} onClick={() => { setConfirmEvent(null); setPage(v => v - 1); }}>上一页</Button>
          <span className="text-sm">第 {page + 1} 页</span><Button variant="outline" disabled={!data.has_more} onClick={() => { setConfirmEvent(null); setPage(v => v + 1); }}>下一页</Button></div>
      </>}
      {confirmEvent && <div role="alert" className="border rounded-lg p-3 space-y-2 text-sm">
        <p>将采购价恢复为 {priceText(confirmEvent.after, "price")}，卖价恢复为 {priceText(confirmEvent.after, "contract_price")}，并恢复该记录中已有的价格有效期、币种、单位、包装及供应商/地点。系统会保留此次恢复记录。</p>
        <div className="flex gap-2"><Button disabled={restoring} onClick={restore}>{restoring ? "恢复中…" : "确认恢复"}</Button><Button variant="outline" disabled={restoring} onClick={() => setConfirmEvent(null)}>取消</Button></div>
      </div>}
    </DialogContent>
  </Dialog>;
}
