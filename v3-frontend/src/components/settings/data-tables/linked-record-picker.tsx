"use client";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { searchLinkTargets } from "@/lib/data-tables-api";
import type { DataRecord, Page, LinkedLabel } from "@/lib/data-tables-types";
import { DATA_TABLES_PATH } from "@/lib/dashboard-routes";
import { ErrorNotice, Pager } from "./shared";

export function LinkedRecordPicker({
  tableId,
  fieldId,
  value,
  onChange,
  disabled = false,
  initialLabel,
}: {
  tableId: string;
  fieldId: string;
  value: string | null;
  onChange: (id: string | null) => void;
  disabled?: boolean;
  initialLabel?: LinkedLabel;
}) {
  const [q, setQ] = useState(""),
    [page, setPage] = useState(1),
    [data, setData] = useState<Page<DataRecord>>();
  const [picked, setPicked] = useState<DataRecord | null>(null),
    [error, setError] = useState<unknown>(),
    [loading, setLoading] = useState(false),
    [refresh, setRefresh] = useState(0);
  const seq = useRef(0);
  useEffect(() => {
    if (disabled) return;
    const n = ++seq.current;
    setLoading(true);
    setData(undefined);
    setError(null);
    searchLinkTargets(tableId, fieldId, { q, page })
      .then((d) => {
        if (n === seq.current) {
          setData(d);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (n === seq.current) {
          setError(e);
          setLoading(false);
        }
      });
    return () => {
      seq.current++;
    };
  }, [tableId, fieldId, q, page, disabled, refresh]);
  const current =
    picked?.id === value
      ? picked
      : initialLabel?.record_id === value
        ? initialLabel
        : null;
  return (
    <div className="border rounded-md p-3 space-y-2">
      {value ? (
        <div className="text-xs break-all">
          已选择：{current?.display_label || "记录"} · {value}
          {current?.status === "archived" && "（已归档）"}
          {current && (
            <a
              className="underline ml-2"
              href={`${DATA_TABLES_PATH}/${current.table_id}?tab=records&record=${value}`}
              target="_blank"
              rel="noreferrer"
            >
              查看目标
            </a>
          )}
          {!disabled && (
            <Button
              type="button"
              size="xs"
              variant="ghost"
              onClick={() => {
                setPicked(null);
                onChange(null);
              }}
            >
              解除关联
            </Button>
          )}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">尚未关联</p>
      )}
      {!disabled && (
        <>
          <Input
            aria-label="搜索关联记录"
            placeholder="搜索显示名称或记录编号"
            maxLength={200}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
          />
          <ErrorNotice error={error} />
          {error && (
            <Button
              type="button"
              variant="outline"
              size="xs"
              onClick={() => setRefresh((v) => v + 1)}
            >
              重新加载候选记录
            </Button>
          )}
          {loading ? (
            <p role="status" className="text-xs">
              搜索中…
            </p>
          ) : (
            data && (
              <>
                <div className="max-h-48 overflow-auto space-y-1">
                  {data.items.map((r) => (
                    <Button
                      className="w-full h-auto py-2 justify-start whitespace-normal text-left break-all"
                      type="button"
                      variant={r.id === value ? "secondary" : "ghost"}
                      size="sm"
                      key={r.id}
                      aria-label={`选择 ${r.display_label} · ${r.id}`}
                      onClick={() => {
                        setPicked(r);
                        onChange(r.id);
                      }}
                    >
                      {r.display_label} ·{" "}
                      <span className="text-xs text-muted-foreground">
                        {r.id}
                      </span>
                    </Button>
                  ))}
                  {!data.items.length && (
                    <p className="text-xs text-muted-foreground">
                      没有符合条件的启用记录。
                    </p>
                  )}
                </div>
                <Pager page={page} total={data.total} onPage={setPage} />
              </>
            )
          )}
        </>
      )}
    </div>
  );
}
