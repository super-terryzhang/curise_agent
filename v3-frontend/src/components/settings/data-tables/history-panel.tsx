"use client";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { listChanges } from "@/lib/data-tables-api";
import type { DataChange, Page, Value } from "@/lib/data-tables-types";
import { displayValue, formatTime, TYPE_LABELS } from "@/lib/data-tables-view";
import { CELL, SELECT_CLASS, ErrorNotice, Pager } from "./shared";

const ACTIONS: Record<string, string> = {
  create_table: "新建表",
  update_table: "修改表",
  archive_table: "归档表",
  restore_table: "恢复表",
  create_field: "新增字段",
  update_field: "修改字段",
  reorder_fields: "字段排序",
  archive_field: "归档字段",
  restore_field: "恢复字段",
  create_record: "新增记录",
  update_record: "编辑记录",
  archive_record: "归档记录",
  restore_record: "恢复记录",
};
const META: Record<string, string> = {
  name: "名称",
  description: "说明",
  display_field_id: "显示名称字段",
  label: "字段名称",
  field_type: "字段类型",
  required: "必填",
  unique: "唯一",
  default_value: "默认值",
  config: "类型配置",
  target_table_id: "关联目标表",
  status: "状态",
  field_ids: "字段顺序",
  sort_order: "顺序",
  schema_version: "结构版本",
  revision: "记录版本",
};
function changes(h: DataChange) {
  const before = h.before || {},
    after = h.after || {},
    rows: { id: string; label: string; old: string; next: string }[] = [];
  function value(key: string, v: unknown, record: boolean): string {
    if (v === undefined || v === null) return "—";
    const f = h.display_snapshot.fields?.[key];
    if (record && f) {
      if (f.field_type === "link") {
        const t = h.display_snapshot.links?.[String(v)];
        return t
          ? `${t.table_name} · ${t.display_label} · ${t.record_id}`
          : String(v);
      }
      return displayValue(f, v as Value);
    }
    if (key === "display_field_id")
      return h.display_snapshot.fields?.[String(v)]?.label || String(v);
    if (key === "target_table_id")
      return h.display_snapshot.target_tables?.[String(v)] || String(v);
    if (key === "field_type")
      return TYPE_LABELS[v as keyof typeof TYPE_LABELS] || String(v);
    if (key === "status")
      return v === "active" ? "启用" : v === "archived" ? "归档" : String(v);
    if (typeof v === "boolean") return v ? "是" : "否";
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
  }
  const bv = (before.values || {}) as Record<string, unknown>,
    av = (after.values || {}) as Record<string, unknown>;
  for (const id of new Set([...Object.keys(bv), ...Object.keys(av)]))
    if (JSON.stringify(bv[id]) !== JSON.stringify(av[id]))
      rows.push({
        id,
        label: h.display_snapshot.fields?.[id]?.label || `字段 ${id}`,
        old: value(id, bv[id], true),
        next: value(id, av[id], true),
      });
  for (const key of new Set([...Object.keys(before), ...Object.keys(after)]))
    if (
      key !== "values" &&
      key !== "id" &&
      JSON.stringify(before[key]) !== JSON.stringify(after[key])
    )
      rows.push({
        id: key,
        label: META[key] || key,
        old: value(key, before[key], false),
        next: value(key, after[key], false),
      });
  return rows;
}
export function HistoryPanel({
  tableId,
  system = false,
}: {
  tableId: string;
  system?: boolean;
}) {
  const [page, setPage] = useState(1),
    [entity, setEntity] = useState(""),
    [data, setData] = useState<Page<DataChange>>(),
    [error, setError] = useState<unknown>(),
    [loading, setLoading] = useState(true),
    [detail, setDetail] = useState<DataChange | null>(null),
    [refresh, setRefresh] = useState(0);
  const seq = useRef(0);
  useEffect(() => {
    const n = ++seq.current;
    setLoading(true);
    setError(null);
    setDetail(null);
    listChanges(tableId, { page, entity_type: entity || undefined })
      .then((d) => {
        if (n === seq.current) setData(d);
      })
      .catch((e) => {
        if (n === seq.current) setError(e);
      })
      .finally(() => {
        if (n === seq.current) setLoading(false);
      });
    return () => {
      seq.current++;
    };
  }, [tableId, page, entity, refresh]);
  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">
        {system
          ? "历史只记录扩展信息的修改；核心业务字段仍由原业务页面及其流程负责。"
          : "历史只读，使用修改时的字段、选项与关联名称；不会因后来改名而改写。"}
      </p>
      <ErrorNotice error={error} />
      <div className="flex gap-2">
        <label className="text-sm">
          对象{" "}
          <select
            className={SELECT_CLASS}
            value={entity}
            onChange={(e) => {
              setEntity(e.target.value);
              setPage(1);
            }}
          >
            <option value="">全部</option>
            <option value="table">表结构</option>
            <option value="field">字段</option>
            <option value="record">数据记录</option>
          </select>
        </label>
        <Button variant="outline" onClick={() => setRefresh((v) => v + 1)}>
          刷新历史
        </Button>
      </div>
      {detail && (
        <section className="border rounded-lg p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium">
              {ACTIONS[detail.action] || detail.action} ·{" "}
              {formatTime(detail.created_at)}
            </h2>
            <Button size="xs" variant="outline" onClick={() => setDetail(null)}>
              关闭详情
            </Button>
          </div>
          <p className="text-xs break-all">
            对象编号 {detail.entity_id} · 操作人 #{detail.actor_id}（
            {detail.actor_role}） · 结构版本 {detail.schema_version}
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/40">
                <tr>
                  {["字段（当时名称）", "旧值", "新值"].map((l) => (
                    <th key={l} className={CELL}>
                      {l}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {changes(detail).map((r) => (
                  <tr key={r.id}>
                    <td className={CELL}>{r.label}</td>
                    <td
                      className={
                        CELL + " whitespace-pre-wrap break-all max-w-96"
                      }
                    >
                      {r.old}
                    </td>
                    <td
                      className={
                        CELL + " whitespace-pre-wrap break-all max-w-96"
                      }
                    >
                      {r.next}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {loading ? (
        <p role="status" className="text-sm">
          正在加载…
        </p>
      ) : (
        data && (
          <>
            <div className="border rounded-lg overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-muted/40">
                  <tr>
                    {[
                      "操作时间（日本）",
                      "操作人",
                      "对象编号",
                      "操作",
                      "详情",
                    ].map((l) => (
                      <th key={l} className={CELL}>
                        {l}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((h) => (
                    <tr key={h.id}>
                      <td className={CELL}>{formatTime(h.created_at)}</td>
                      <td className={CELL}>
                        #{h.actor_id} · {h.actor_role}
                      </td>
                      <td className={CELL + " text-xs break-all"}>
                        {h.entity_id}
                      </td>
                      <td className={CELL}>{ACTIONS[h.action] || h.action}</td>
                      <td className={CELL}>
                        <Button
                          size="xs"
                          variant="outline"
                          onClick={() => setDetail(h)}
                        >
                          查看变更
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!data.items.length && (
                <p className="p-5 text-sm text-muted-foreground">
                  暂无修改历史。
                </p>
              )}
            </div>
            <Pager page={page} total={data.total} onPage={setPage} />
          </>
        )
      )}
    </div>
  );
}
