import type { OrderIssueFinding, OrderIssueRow } from "./orders-api";

export type OrderIssueFilter =
  | "all"
  | "not_matched"
  | "matched_with_issues"
  | "inquiry_ready";

export function normalizedMatchStatus(
  value: string | null | undefined,
): "matched" | "not_matched" {
  return value === "matched" ? "matched" : "not_matched";
}

export function filterIssueRows(
  rows: OrderIssueRow[],
  filter: OrderIssueFilter,
): OrderIssueRow[] {
  if (filter === "all") return rows;
  if (filter === "not_matched") {
    return rows.filter((row) => normalizedMatchStatus(row.match_status) === "not_matched");
  }
  if (filter === "matched_with_issues") {
    return rows.filter(
      (row) => normalizedMatchStatus(row.match_status) === "matched" && row.findings.length > 0,
    );
  }
  return rows.filter(
    (row) => row.inquiry_disposition === "included"
      || row.inquiry_disposition === "included_with_warning",
  );
}

export function issueStatusText(row: OrderIssueRow): {
  match: string;
  inquiry: string;
} {
  const match = normalizedMatchStatus(row.match_status) === "matched" ? "匹配成功" : "未匹配";
  const inquiry = row.inquiry_disposition === "excluded"
    ? "不进入询价"
    : row.inquiry_disposition === "included_with_warning"
      ? "可询价，需复核"
      : "可询价";
  return { match, inquiry };
}

export function issueSummary(row: OrderIssueRow): string {
  if (row.findings.length === 0) return "检查通过";
  const messages = row.findings.map((finding) => finding.message || finding.code);
  if (messages.length === 1) return messages[0];
  return `${messages.length} 个问题：${messages.join("；")}`;
}

export function resolutionLabel(finding: OrderIssueFinding): string {
  return finding.resolution?.label?.trim() || "查看并人工处理";
}
