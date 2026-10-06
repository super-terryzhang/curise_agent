"use client";
import { useParams, useSearchParams } from "next/navigation";
import { TableDetail } from "@/components/settings/data-tables/table-detail";
import { parseTableTab } from "@/lib/data-tables-view";
export default function DataTablePage() {
  const params = useParams(),
    q = useSearchParams();
  return (
    <TableDetail
      tableId={String(params.tableId)}
      activeTab={parseTableTab(q.get("tab"))}
      recordId={q.get("record")}
    />
  );
}
