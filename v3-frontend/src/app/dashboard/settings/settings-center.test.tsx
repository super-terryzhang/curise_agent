// @vitest-environment jsdom
import React from "react";
import "@/test/setup-dom";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { SettingsCenter, normalizeSettingsSection } from "./settings-center";

const settingsApi = vi.hoisted(() => ({
  getCompanyConfig: vi.fn(),
}));

vi.mock("@/lib/settings-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/settings-api")>()),
  getCompanyConfig: settingsApi.getCompanyConfig,
}));

beforeEach(() => {
  settingsApi.getCompanyConfig.mockReset();
  settingsApi.getCompanyConfig.mockResolvedValue([]);
});

it("shows the two setting groups and six canonical entries", () => {
  render(<SettingsCenter />);

  expect(screen.getByRole("heading", { name: "订单与询价" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "公司与系统" })).toBeTruthy();

  const expectedLinks = [
    ["订单提取字段", "/dashboard/settings?section=fields"],
    ["订单格式", "/dashboard/settings?section=orders"],
    ["供应商询价模板", "/dashboard/settings?section=suppliers"],
    ["配送点", "/dashboard/settings?section=delivery"],
    ["公司信息", "/dashboard/settings?section=company"],
    ["AI 配置", "/dashboard/settings?section=ai"],
  ];
  for (const [name, href] of expectedLinks) {
    expect(
      screen.getByRole("link", { name: new RegExp(name) }).getAttribute("href"),
    ).toBe(href);
  }

  expect(screen.queryByText("数据表管理")).toBeNull();
  expect(screen.queryByRole("tablist")).toBeNull();
});

it("opens only the selected existing setting and provides a stable return link", async () => {
  render(<SettingsCenter section="company" />);

  expect(
    screen.getByRole("link", { name: "返回设置中心" }).getAttribute("href"),
  ).toBe("/dashboard/settings");
  await waitFor(() =>
    expect(screen.getByText("询价单中使用的公司联系方式")).toBeTruthy(),
  );
  expect(screen.queryByRole("heading", { name: "订单与询价" })).toBeNull();
});

it.each(["", "unknown"])(
  "returns to the categorized home for unsupported section %j",
  (section) => {
    render(<SettingsCenter section={section} />);
    expect(screen.getByRole("heading", { name: "订单与询价" })).toBeTruthy();
  },
);

it("rejects repeated section parameters instead of guessing", () => {
  expect(normalizeSettingsSection(["company", "ai"])).toBeUndefined();
  expect(normalizeSettingsSection("company")).toBe("company");
  expect(normalizeSettingsSection(undefined)).toBeUndefined();
});
