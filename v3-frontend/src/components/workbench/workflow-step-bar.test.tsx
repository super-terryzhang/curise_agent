import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { WorkflowStepBar } from "./workflow-step-bar";

describe("WorkflowStepBar", () => {
  it("renders the existing four-step upload flow without changing its labels", () => {
    const html = renderToStaticMarkup(
      <WorkflowStepBar
        current={2}
        labels={["上传文件", "程序检查", "核对变更", "确认提交"]}
      />,
    );

    expect(html).toContain("上传文件");
    expect(html).toContain("程序检查");
    expect(html).toContain("核对变更");
    expect(html).toContain("确认提交");
    expect(html).toContain('aria-current="step"');
  });

  it("renders the approved five-stage existing-product flow", () => {
    const html = renderToStaticMarkup(
      <WorkflowStepBar
        current={3}
        labels={["选择产品", "更新范围", "下载与上传", "程序检查", "核对与提交"]}
      />,
    );

    for (const label of ["选择产品", "更新范围", "下载与上传", "程序检查", "核对与提交"]) {
      expect(html).toContain(label);
    }
    expect(html.match(/aria-current="step"/g)).toHaveLength(1);
  });
});
