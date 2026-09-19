import { describe, expect, it } from "vitest";

import { oracleIssueMessage, oracleRunErrorMessage } from "./oracle-scan-messages";

describe("Oracle scan messages", () => {
  it("explains a deferred missing revision without calling the entire scan failed", () => {
    expect(oracleIssueMessage({ code: "ORACLE_INVALID_IDENTITY", field: "Revision" }, "deferred"))
      .toBe("Oracle PO 版本号不可用，下一轮会自动重查");
  });

  it("asks for review when an OPEN PO has an invalid revision", () => {
    expect(oracleIssueMessage({ code: "ORACLE_INVALID_IDENTITY", field: "Revision" }, "needs_review"))
      .toBe("Oracle PO 版本号不可用，请人工核查");
  });

  it("explains a page-level failure and retains its diagnostic code", () => {
    expect(oracleRunErrorMessage("ORACLE_PAGINATION_INVALID"))
      .toBe("Oracle 返回的订单列表结构异常，扫描未完成（ORACLE_PAGINATION_INVALID）");
  });

  it("does not imply a legacy generic error has a known cause", () => {
    expect(oracleRunErrorMessage("SCAN_INTEGRATIONERROR"))
      .toBe("旧扫描记录未保存具体原因；可查看最新扫描结果或联系管理员");
  });
});
