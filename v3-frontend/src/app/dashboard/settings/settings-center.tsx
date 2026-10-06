"use client";

import type { ComponentType } from "react";
import type { LucideIcon } from "lucide-react";
import {
  ArrowLeft,
  Bot,
  Building2,
  ChevronRight,
  FileSpreadsheet,
  ListChecks,
  MapPin,
  ReceiptText,
} from "lucide-react";

import { PageHeader } from "@/components/page-header";
import AIConfigTab from "./AIConfigTab";
import CompanyConfigTab from "./CompanyConfigTab";
import DeliveryLocationTab from "./DeliveryLocationTab";
import FieldSchemaTab from "./FieldSchemaTab";
import OrderFormatTab from "./OrderFormatTab";
import SupplierTemplateTab from "./SupplierTemplateTab";

type SettingsSectionId =
  | "fields"
  | "orders"
  | "suppliers"
  | "delivery"
  | "company"
  | "ai";

interface SettingsEntry {
  id: SettingsSectionId;
  title: string;
  description: string;
  icon: LucideIcon;
  component: ComponentType;
}

interface SettingsGroup {
  title: string;
  description: string;
  entries: SettingsEntry[];
}

const SETTINGS_GROUPS: SettingsGroup[] = [
  {
    title: "订单与询价",
    description: "管理订单识别规则与供应商询价文件。",
    entries: [
      {
        id: "fields",
        title: "订单提取字段",
        description: "定义订单提取时使用的字段结构",
        icon: ListChecks,
        component: FieldSchemaTab,
      },
      {
        id: "orders",
        title: "订单格式",
        description: "管理客户订单的识别与解析格式",
        icon: FileSpreadsheet,
        component: OrderFormatTab,
      },
      {
        id: "suppliers",
        title: "供应商询价模板",
        description: "管理供应商询价单模板与字段位置",
        icon: ReceiptText,
        component: SupplierTemplateTab,
      },
    ],
  },
  {
    title: "公司与系统",
    description: "维护询价资料、公司信息和 AI 能力。",
    entries: [
      {
        id: "delivery",
        title: "配送点",
        description: "维护询价单使用的配送地址",
        icon: MapPin,
        component: DeliveryLocationTab,
      },
      {
        id: "company",
        title: "公司信息",
        description: "维护询价单使用的公司联系方式",
        icon: Building2,
        component: CompanyConfigTab,
      },
      {
        id: "ai",
        title: "AI 配置",
        description: "管理 AI 工具与技能配置",
        icon: Bot,
        component: AIConfigTab,
      },
    ],
  },
];

const SETTINGS_ENTRIES = SETTINGS_GROUPS.flatMap((group) => group.entries);

export function normalizeSettingsSection(
  section: string | string[] | undefined,
): string | undefined {
  return typeof section === "string" ? section : undefined;
}

export function SettingsCenter({ section }: { section?: string }) {
  const selected = SETTINGS_ENTRIES.find((entry) => entry.id === section);

  if (selected) {
    const SelectedComponent = selected.component;
    return (
      <div className="h-full overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-6xl space-y-5">
          <a
            href="/dashboard/settings"
            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            返回设置中心
          </a>
          <PageHeader title={selected.title} description={selected.description} />
          <div className="border-t pt-5">
            <SelectedComponent />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="mx-auto max-w-6xl space-y-7">
        <PageHeader title="设置中心" description="选择需要维护的系统配置。" />

        {SETTINGS_GROUPS.map((group) => (
          <section key={group.title} aria-labelledby={`settings-${group.title}`}>
            <div className="mb-3">
              <h2
                id={`settings-${group.title}`}
                className="text-sm font-semibold text-foreground"
              >
                {group.title}
              </h2>
              <p className="mt-1 text-xs text-muted-foreground">
                {group.description}
              </p>
            </div>
            <div className="overflow-hidden rounded-lg border bg-card">
              {group.entries.map((entry) => {
                const Icon = entry.icon;
                return (
                  <a
                    key={entry.id}
                    href={`/dashboard/settings?section=${entry.id}`}
                    className="group flex min-h-20 items-center gap-4 border-b px-5 py-4 transition-colors last:border-b-0 hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset"
                  >
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border bg-background text-muted-foreground group-hover:text-primary">
                      <Icon className="h-4 w-4" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium text-foreground">
                        {entry.title}
                      </span>
                      <span className="mt-1 block text-xs text-muted-foreground">
                        {entry.description}
                      </span>
                    </span>
                    <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                  </a>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
