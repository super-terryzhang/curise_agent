"use client";

import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { DATA_TABLES_PATH } from "@/lib/dashboard-routes";

export function LegacyDataTablesRedirect({ tableId }: { tableId?: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const query = searchParams.toString();
  const destination = `${DATA_TABLES_PATH}${
    tableId ? `/${encodeURIComponent(tableId)}` : ""
  }${query ? `?${query}` : ""}`;

  useEffect(() => {
    router.replace(destination);
  }, [destination, router]);

  return (
    <p className="p-6 text-sm text-muted-foreground" role="status">
      正在转到数据表管理…
    </p>
  );
}
