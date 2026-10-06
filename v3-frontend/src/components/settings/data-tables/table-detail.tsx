"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { getUser } from "@/lib/auth";
import { canManageRecords } from "@/lib/data-tables-view";
import {
  getTable,
  listFields,
  DataTablesApiError,
} from "@/lib/data-tables-api";
import type { DataTable, DataField } from "@/lib/data-tables-types";
import { DATA_TABLES_PATH } from "@/lib/dashboard-routes";
import { FieldsPanel } from "./fields-panel";
import { ErrorNotice } from "./shared";
import { RecordsPanel } from "./records-panel";
import { HistoryPanel } from "./history-panel";

export function TableDetail({
  tableId,
  activeTab = "fields",
  recordId,
}: {
  tableId: string;
  activeTab: "fields" | "records" | "history";
  recordId?: string | null;
}) {
  const allowed = canManageRecords(getUser()?.role),
    seq = useRef(0);
  const [snapshot, setSnapshot] = useState<{
      table: DataTable;
      fields: DataField[];
    }>(),
    [error, setError] = useState<unknown>();
  const load = useCallback(async () => {
    const n = ++seq.current;
    setError(null);
    try {
      const [table, fields] = await Promise.all([
        getTable(tableId),
        listFields(tableId),
      ]);
      if (n === seq.current) setSnapshot({ table, fields });
    } catch (e) {
      if (n === seq.current) {
        setError(e);
        if (e instanceof DataTablesApiError && e.status === 503)
          setSnapshot(undefined);
      }
    }
  }, [tableId]);
  useEffect(() => {
    setSnapshot(undefined);
    if (allowed) void load();
    return () => {
      seq.current++;
    };
  }, [allowed, load]);
  return (
    <div className="h-full overflow-auto p-6 space-y-5">
      <a
        className="text-xs underline text-muted-foreground"
        href={DATA_TABLES_PATH}
      >
        返回数据表管理
      </a>
      <PageHeader
        title={snapshot?.table.name || "数据表"}
        description={
          snapshot?.table.description || "配置字段、维护记录并查看历史。"
        }
        action={
          allowed && (
            <Button variant="outline" onClick={() => void load()}>
              刷新配置
            </Button>
          )
        }
      />
      <p className="text-xs text-muted-foreground">
        公司授权角色共享访问。
        {snapshot?.table.status === "archived" &&
          "此表已归档，只读；请从列表恢复后编辑。"}
      </p>
      {!allowed ? (
        <p className="text-sm">当前角色无权访问数据表管理。</p>
      ) : (
        <>
          <ErrorNotice error={error} />
          {snapshot ? (
            <>
              <nav aria-label="表详情" className="flex gap-1 border-b pb-2">
                {[
                  ["fields", "字段配置"],
                  ["records", "数据记录"],
                  ["history", "修改历史"],
                ].map(([v, l]) => (
                  <Button
                    key={v}
                    variant={activeTab === v ? "secondary" : "ghost"}
                    asChild
                  >
                    <a
                      href={`${DATA_TABLES_PATH}/${tableId}?tab=${v}`}
                      aria-current={activeTab === v ? "page" : undefined}
                    >
                      {l}
                    </a>
                  </Button>
                ))}
              </nav>
              {activeTab === "fields" ? (
                <FieldsPanel
                  table={snapshot.table}
                  fields={snapshot.fields}
                  onChanged={() => void load()}
                />
              ) : activeTab === "records" ? (
                <RecordsPanel
                  table={snapshot.table}
                  fields={snapshot.fields}
                  recordId={recordId}
                  onChanged={() => void load()}
                />
              ) : (
                <HistoryPanel
                  tableId={tableId}
                  system={snapshot.table.table_kind === "system"}
                />
              )}
            </>
          ) : (
            !error && (
              <p role="status" className="text-sm">
                正在加载…
              </p>
            )
          )}
        </>
      )}
    </div>
  );
}
