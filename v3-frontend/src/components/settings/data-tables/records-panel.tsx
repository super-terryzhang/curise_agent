"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getUser } from "@/lib/auth";
import * as api from "@/lib/data-tables-api";
import type {
  DataTable,
  DataField,
  DataRecord,
  Page,
  Status,
  RecordFilter,
  Value,
} from "@/lib/data-tables-types";
import { canManageRecords, displayValue } from "@/lib/data-tables-view";
import { RecordEditor } from "./record-editor";
import { ValueControl } from "./value-control";
import { CELL, SELECT_CLASS, ErrorNotice, Pager } from "./shared";

const operators: Record<string, RecordFilter["operator"][]> = {
  text: ["eq", "contains"],
  number: ["eq", "lt", "lte", "gt", "gte"],
  date: ["eq", "lt", "lte", "gt", "gte"],
  datetime: ["eq", "lt", "lte", "gt", "gte"],
  boolean: ["eq"],
  single_select: ["eq"],
  multi_select: ["contains"],
  link: ["eq"],
};
const labels = {
  eq: "等于",
  contains: "包含",
  lt: "小于",
  lte: "小于或等于",
  gt: "大于",
  gte: "大于或等于",
  is_empty: "为空",
};
export function RecordsPanel({
  table,
  fields,
  onChanged,
  recordId,
}: {
  table: DataTable;
  fields: DataField[];
  onChanged: () => void;
  recordId?: string | null;
}) {
  const active = fields.filter((f) => f.status === "active"),
    writable = canManageRecords(getUser()?.role) && table.status === "active",
    system = table.table_kind === "system";
  const [status, setStatus] = useState<Status>("active"),
    [page, setPage] = useState(1),
    [sort, setSort] = useState(""),
    [direction, setDirection] = useState<"asc" | "desc">("asc");
  const [filterId, setFilterId] = useState(""),
    [operator, setOperator] = useState<RecordFilter["operator"]>("eq"),
    [filterValue, setFilterValue] = useState<Value>(null),
    [filters, setFilters] = useState<RecordFilter[]>([]);
  const [searchDraft, setSearchDraft] = useState(""),
    [search, setSearch] = useState("");
  const [data, setData] = useState<Page<DataRecord>>(),
    [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<unknown>();
  const [editor, setEditor] = useState<DataRecord | "new" | null>(null),
    [confirmation, setConfirmation] = useState<DataRecord | null>(null);
  const sequence = useRef(0);
  const load = useCallback(async () => {
    const n = ++sequence.current;
    setLoading(true);
    setError(null);
    try {
      const d = await api.listRecords(
        table.id,
        system
          ? { page, q: search || undefined }
          : {
              page,
              status,
              sort_field_id: sort || undefined,
              sort_direction: direction,
              filters,
            },
      );
      if (n === sequence.current) setData(d);
    } catch (e) {
      if (n === sequence.current) setError(e);
    } finally {
      if (n === sequence.current) setLoading(false);
    }
  }, [table.id, system, page, status, sort, direction, filters, search]);
  useEffect(() => {
    void load();
    return () => {
      sequence.current++;
    };
  }, [load]);
  useEffect(() => {
    if (!recordId) return;
    let live = true;
    api
      .getRecord(table.id, recordId)
      .then((r) => {
        if (live) setEditor(r);
      })
      .catch((e) => {
        if (live) setError(e);
      });
    return () => {
      live = false;
    };
  }, [table.id, recordId]);
  async function changeStatus() {
    if (!confirmation) return;
    setBusy(true);
    setError(null);
    try {
      await (
        confirmation.status === "active" ? api.archiveRecord : api.restoreRecord
      )(table.id, confirmation.id, {
        expected_revision: confirmation.revision,
        schema_version: table.schema_version,
      });
      setConfirmation(null);
      await load();
      onChanged();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  const filter = active.find((f) => f.id === filterId);
  return (
    <div className="space-y-4">
      <ErrorNotice error={error} />
      {editor && (
        <RecordEditor
          key={editor === "new" ? "new" : editor.id}
          table={table}
          fields={fields}
          record={editor === "new" ? undefined : editor}
          onSaved={() => {
            setEditor(null);
            void load();
            onChanged();
          }}
          onCancel={() => setEditor(null)}
        />
      )}
      <div className="flex flex-wrap gap-3 items-center">
        {system ? (
          <>
            <label className="text-sm flex items-center gap-2">
              搜索核心信息
              <Input
                className="w-72"
                value={searchDraft}
                disabled={!!editor}
                onChange={(e) => setSearchDraft(e.target.value)}
              />
            </label>
            <Button
              variant="outline"
              disabled={loading || busy || !!editor}
              onClick={() => {
                setSearch(searchDraft.trim());
                setPage(1);
              }}
            >
              搜索
            </Button>
          </>
        ) : (
          <>
        <label className="text-sm">
          状态{" "}
          <select
            className={SELECT_CLASS}
            disabled={!!editor}
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as Status);
              setPage(1);
            }}
          >
            <option value="active">启用</option>
            <option value="archived">归档</option>
          </select>
        </label>
        <label className="text-sm">
          排序{" "}
          <select
            className={SELECT_CLASS}
            disabled={!!editor}
            value={sort}
            onChange={(e) => {
              setSort(e.target.value);
              setPage(1);
            }}
          >
            <option value="">创建时间</option>
            {active
              .filter(
                (f) =>
                  f.field_type !== "link" && f.field_type !== "multi_select",
              )
              .map((f) => (
                <option key={f.id} value={f.id}>
                  {f.label}
                </option>
              ))}
          </select>
        </label>
        <select
          className={SELECT_CLASS}
          aria-label="排序方向"
          disabled={!!editor}
          value={direction}
          onChange={(e) => {
            setDirection(e.target.value as "asc" | "desc");
            setPage(1);
          }}
        >
          <option value="asc">升序</option>
          <option value="desc">降序</option>
        </select>
          </>
        )}
        <Button
          variant="outline"
          disabled={loading || busy}
          onClick={() => void load()}
        >
          刷新记录
        </Button>
        {writable && !system && (
          <Button
            disabled={busy || !!editor || !active.length}
            onClick={() => setEditor("new")}
          >
            新增记录
          </Button>
        )}
      </div>
      {!system && <div className="border rounded-md p-3 space-y-2">
        <div className="flex flex-wrap gap-2 items-center">
          <select
            className={SELECT_CLASS}
            aria-label="筛选字段"
            value={filterId}
            onChange={(e) => {
              setFilterId(e.target.value);
              setFilterValue(null);
              setOperator(
                operators[
                  active.find((f) => f.id === e.target.value)?.field_type ||
                    "text"
                ]?.[0] || "eq",
              );
            }}
          >
            <option value="">选择筛选字段</option>
            {active.map((f) => (
              <option value={f.id} key={f.id}>
                {f.label}
              </option>
            ))}
          </select>
          {filter && (
            <select
              className={SELECT_CLASS}
              aria-label="筛选条件"
              value={operator}
              onChange={(e) =>
                setOperator(e.target.value as RecordFilter["operator"])
              }
            >
              {[
                ...(operators[filter.field_type] || []),
                "is_empty" as const,
              ].map((o) => (
                <option key={o} value={o}>
                  {labels[o]}
                </option>
              ))}
            </select>
          )}
          <Button
            variant="outline"
            disabled={busy || !!editor || !filter}
            onClick={() => {
              setFilters([
                {
                  field_id: filterId,
                  operator,
                  ...(operator === "is_empty" ? {} : { value: filterValue }),
                },
              ]);
              setPage(1);
            }}
          >
            应用筛选
          </Button>
          <Button
            variant="ghost"
            disabled={busy || !!editor}
            onClick={() => {
              setFilters([]);
              setFilterId("");
              setPage(1);
            }}
          >
            清除筛选
          </Button>
        </div>
        {filter && operator !== "is_empty" && (
          <div className="max-w-lg">
            <ValueControl
              field={
                filter.field_type === "multi_select"
                  ? { ...filter, field_type: "single_select" }
                  : filter
              }
              value={filterValue}
              label="筛选值"
              onChange={setFilterValue}
            />
          </div>
        )}
      </div>}
      {!system && confirmation && (
        <div
          role="dialog"
          aria-label="确认记录状态"
          className="border rounded-lg p-4 space-y-2"
        >
          <p className="text-sm">
            {confirmation.status === "active" ? "归档" : "恢复"}记录“
            {confirmation.display_label}”？保留原值与修改历史。
          </p>
          <Button disabled={busy} onClick={() => void changeStatus()}>
            确认{confirmation.status === "active" ? "归档" : "恢复"}
          </Button>{" "}
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => setConfirmation(null)}
          >
            取消
          </Button>
        </div>
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
                    <th className={CELL}>记录编号</th>
                    {active.map((f) => (
                      <th className={CELL + " min-w-40"} key={f.id}>
                        {f.label}
                      </th>
                    ))}
                    <th className={CELL}>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((r) => (
                    <tr key={r.id}>
                      <td className={CELL + " text-xs break-all min-w-56"}>
                        {r.id}
                        <p className="text-muted-foreground">
                          版本 {r.revision}
                        </p>
                      </td>
                      {active.map((f) => (
                        <td
                          className={
                            CELL + " max-w-80 whitespace-pre-wrap break-words"
                          }
                          key={f.id}
                        >
                          {f.field_type === "link" &&
                          r.linked_labels?.[f.id] ? (
                            <>
                              <a
                                className="underline"
                                href={`/dashboard/settings/data-tables/${r.linked_labels[f.id].table_id}?tab=records&record=${r.linked_labels[f.id].record_id}`}
                              >
                                {r.linked_labels[f.id].display_label}
                              </a>
                              <p className="text-xs text-muted-foreground break-all">
                                {r.linked_labels[f.id].table_name} ·{" "}
                                {r.linked_labels[f.id].record_id}
                                {r.linked_labels[f.id].status === "archived"
                                  ? " · 已归档"
                                  : ""}
                              </p>
                            </>
                          ) : (
                            displayValue(f, r.values[f.id])
                          )}
                        </td>
                      ))}
                      <td className={CELL}>
                        <div className="flex gap-2">
                          {system && r.business_url && (
                            <a className="underline" href={r.business_url}>
                              打开业务页面
                            </a>
                          )}
                          <Button
                            size="xs"
                            variant="outline"
                            disabled={busy || !!editor}
                            onClick={() => setEditor(r)}
                          >
                            {writable && r.status === "active"
                              ? "编辑"
                              : "查看"}
                          </Button>
                          {writable && !system && (
                            <Button
                              size="xs"
                              variant="outline"
                              disabled={busy || !!editor || !!confirmation}
                              onClick={() => setConfirmation(r)}
                            >
                              {r.status === "active" ? "归档" : "恢复"}
                            </Button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!data.items.length && (
                <p className="p-5 text-sm text-muted-foreground">
                  暂无符合条件的记录。
                </p>
              )}
            </div>
            <Pager
              page={page}
              total={data.total}
              disabled={busy || !!editor}
              onPage={setPage}
            />
          </>
        )
      )}
    </div>
  );
}
