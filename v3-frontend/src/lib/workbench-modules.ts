import {
  Bot,
  Calculator,
  FileSpreadsheet,
  Images,
  type LucideIcon,
} from "lucide-react";

export type WorkbenchRole = "superadmin" | "admin" | "finance" | "employee";

export interface WorkbenchModule {
  key: string;
  title: string;
  description: string;
  href: string | null;
  icon: LucideIcon;
  roles: WorkbenchRole[];
  status: "available" | "planned";
}

export const WORKBENCH_MODULES: WorkbenchModule[] = [
  {
    key: "product-upload",
    title: "产品数据上传",
    description: "上传 Excel，程序检查后核对新增与变更，再统一提交。",
    href: "/dashboard/workbench/product-upload",
    icon: FileSpreadsheet,
    roles: ["superadmin", "admin", "employee"],
    status: "available",
  },
  {
    key: "image-upload",
    title: "产品图片上传",
    description: "批量匹配产品并更新图片。",
    href: "/dashboard/workbench/image-upload",
    icon: Images,
    roles: ["superadmin", "admin", "employee"],
    status: "available",
  },
  {
    key: "ai",
    title: "AI 交流",
    description: "与 AI 助手交流，并查看会话产生的业务结果。",
    href: "/dashboard/workbench/ai",
    icon: Bot,
    roles: ["superadmin", "admin", "employee", "finance"],
    status: "available",
  },
  {
    key: "finance",
    title: "财务工具",
    description: "财务分析与批量处理工具将在这里集中提供。",
    href: null,
    icon: Calculator,
    roles: ["superadmin", "admin", "finance"],
    status: "planned",
  },
];

export function visibleWorkbenchModules(role: string): WorkbenchModule[] {
  return WORKBENCH_MODULES.filter((module) =>
    module.roles.includes(role as WorkbenchRole),
  );
}
