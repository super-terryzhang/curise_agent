// @vitest-environment jsdom
import React from "react";
import "@/test/setup-dom";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import DashboardLayout from "./layout";
import { saveAuth } from "@/lib/auth";
import { TooltipProvider } from "@/components/ui/tooltip";
const route = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  pathname: "/dashboard/settings/data-tables",
}));
vi.mock("next/navigation", () => ({
  useRouter: () => route,
  usePathname: () => route.pathname,
}));
vi.mock("next-themes", () => ({
  useTheme: () => ({ theme: "light", setTheme: vi.fn() }),
}));
// Chat transport is unrelated to navigation and must not connect to a real LLM.
vi.mock("@/components/assistant/AssistantProvider", () => ({
  AssistantProvider: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock("@/components/assistant/AssistantSidebar", () => ({
  AssistantSidebar: () => null,
}));
beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  route.pathname = "/dashboard/settings/data-tables";
});
it.each(["employee", "finance"] as const)(
  "%s can navigate custom records without receiving administrator settings",
  async (role) => {
    saveAuth("synthetic-local-token", {
      id: 1,
      email: "local@example.test",
      full_name: "本地员工",
      role,
      is_active: true,
      is_default_password: false,
    });
    render(
      <TooltipProvider>
        <DashboardLayout>
          <p>自定义表内容</p>
        </DashboardLayout>
      </TooltipProvider>,
    );
    const entry = await screen.findByRole("button", { name: "自定义数据表" });
    await userEvent.setup().click(entry);
    expect(route.push).toHaveBeenCalledWith("/dashboard/settings/data-tables");
    expect(route.replace).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "设置中心" })).toBeNull();
  },
);
it("employee remains denied administrator settings outside the custom module", async () => {
  route.pathname = "/dashboard/settings";
  saveAuth("synthetic-local-token", {
    id: 1,
    email: "local@example.test",
    full_name: "本地员工",
    role: "employee",
    is_active: true,
    is_default_password: false,
  });
  render(
    <TooltipProvider>
      <DashboardLayout>
        <p>设置内容</p>
      </DashboardLayout>
    </TooltipProvider>,
  );
  await waitFor(() =>
    expect(route.replace).toHaveBeenCalledWith("/dashboard/workbench"),
  );
});
