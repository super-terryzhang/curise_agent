"use client";

import { useParams } from "next/navigation";

import { LegacyDataTablesRedirect } from "@/components/settings/data-tables/legacy-data-tables-redirect";

export default function LegacyDataTablePage() {
  const params = useParams();
  return <LegacyDataTablesRedirect tableId={String(params.tableId)} />;
}
