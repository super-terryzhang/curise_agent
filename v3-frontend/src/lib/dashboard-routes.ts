export const DATA_TABLES_PATH = process.env.NEXT_PUBLIC_STANDALONE_DATA_PREPARATION === "true" ? "/prepare/tables" : "/dashboard/data-tables";
export const LEGACY_DATA_TABLES_PATH = "/dashboard/settings/data-tables";

export function isPathWithin(pathname: string, basePath: string): boolean {
  return pathname === basePath || pathname.startsWith(`${basePath}/`);
}

export function canonicalizeDashboardPath(pathname: string): string {
  if (!isPathWithin(pathname, LEGACY_DATA_TABLES_PATH)) return pathname;
  return `${DATA_TABLES_PATH}${pathname.slice(LEGACY_DATA_TABLES_PATH.length)}`;
}
