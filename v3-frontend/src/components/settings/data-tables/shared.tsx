"use client";
import { Button } from "@/components/ui/button";
import { DataTablesApiError } from "@/lib/data-tables-api";
import { DATA_TABLES_PATH } from "@/lib/dashboard-routes";
export const SELECT_CLASS =
  "h-9 rounded-md border bg-background px-3 text-sm min-w-0";
export const CELL = "px-3 py-3 text-left align-top border-b";
export function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null;
  const issues = error instanceof DataTablesApiError ? error.issues : [];
  return (
    <div
      role="alert"
      className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm space-y-2"
    >
      <p>
        {error instanceof Error
          ? error.message
          : "操作未完成，请保留输入并重试"}
      </p>
      {issues.map((i, n) => (
        <p key={n}>
          {i.field_label ? `${i.field_label}：` : ""}
          {i.message}
          {i.record_id && i.table_id && (
            <>
              {" "}
              ·{" "}
              <a
                className="underline"
                href={`${DATA_TABLES_PATH}/${i.table_id}?tab=records&record=${i.record_id}`}
              >
                查看受影响记录 {i.record_id}
              </a>
            </>
          )}
        </p>
      ))}
    </div>
  );
}
export function Pager({
  page,
  total,
  size = 50,
  onPage,
  disabled = false,
}: {
  page: number;
  total: number;
  size?: number;
  onPage: (v: number) => void;
  disabled?: boolean;
}) {
  const pages = Math.max(1, Math.ceil(total / size));
  return (
    <div className="flex items-center justify-end gap-3 py-3 text-xs">
      <span>共 {total} 条</span>
      <Button
        variant="outline"
        size="sm"
        disabled={disabled || page <= 1}
        onClick={() => onPage(page - 1)}
      >
        上一页
      </Button>
      <span>
        {page} / {pages}
      </span>
      <Button
        variant="outline"
        size="sm"
        disabled={disabled || page >= pages}
        onClick={() => onPage(page + 1)}
      >
        下一页
      </Button>
    </div>
  );
}
