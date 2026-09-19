export type OracleIssue = { code: string; field?: string | null };

const reasons: Record<string, string> = {
  TEMPLATE_BINDING_REQUIRED: "供应商尚未绑定询价模板",
  TEMPLATE_FIELDS_REQUIRED: "询价模板字段不完整",
  UNIT_CONVERSION_EVIDENCE_REQUIRED: "采购单位换算待确认",
  UNIT_CONVERSION_INCONSISTENT: "单位换算与需求不一致",
  EXACT_UNIQUE_MATCH_REQUIRED: "商品未唯一匹配",
  SUPPLIER_REQUIRED: "缺少供应商",
  DESTINATION_REQUIRES_REVIEW: "交付港口待确认",
  DELIVERY_DATE_REQUIRED: "缺少交付日期",
  QUANTITY_INVALID: "数量无效",
  UNIT_REQUIRED: "缺少单位",
  MATCH_LINE_COVERAGE: "商品匹配行不完整",
  HISTORICAL_OPEN_REQUIRES_ADOPTION: "已有 OPEN 待确认导入",
  REVISION_REQUIRES_ADOPTION: "PO 已修订，需确认如何更新",
  EXISTING_PO_REQUIRES_LINK: "系统已有同一 PO，需关联",
  SCAN_DISPATCH_FAILED: "启动失败，可重新扫描",
  SCAN_INTERRUPTED: "扫描中断，可重新扫描",
  PO_NO_LONGER_OPEN: "该 PO 已不再是 OPEN，未导入",
  IMPORT_INTERRUPTED_REVIEW_REQUIRED: "处理曾中断，请查看文档后处理",
};

const fields: Record<string, string> = {
  POHeaderId: "Oracle 内部编号",
  OrderNumber: "PO 编号",
  Revision: "版本号",
  StatusCode: "状态",
  CreationDate: "创建日期",
  LastUpdateDate: "更新日期",
  record: "记录内容",
};

export function oracleIssueMessage(issue: OracleIssue, status: string): string {
  if (issue.code === "ORACLE_INVALID_IDENTITY" || issue.code === "ORACLE_INVALID_DATE") {
    const label = fields[issue.field || ""] || "关键字段";
    return `Oracle PO ${label}不可用，${status === "deferred" ? "下一轮会自动重查" : "请人工核查"}`;
  }
  return reasons[issue.code] || `需人工核查（${issue.code}）`;
}

export function oracleRunErrorMessage(code: string): string {
  if (code === "SCAN_INTEGRATIONERROR") {
    return "旧扫描记录未保存具体原因；可查看最新扫描结果或联系管理员";
  }
  if (code === "ORACLE_PAGINATION_INVALID") {
    return "Oracle 返回的订单列表结构异常，扫描未完成（ORACLE_PAGINATION_INVALID）";
  }
  if (code === "ORACLE_NETWORK_ERROR" || code === "ORACLE_HTTP_408" || code === "ORACLE_HTTP_429" || /^ORACLE_HTTP_5\d\d$/.test(code)) {
    return `Oracle 接口暂时不可用，扫描未完成；下一轮将自动重试（${code}）`;
  }
  return reasons[code] || `扫描未完成（${code}），请联系管理员`;
}
