"use client";

import { ArrowRight, BriefcaseBusiness } from "lucide-react";
import { useRouter } from "next/navigation";

import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { getUser } from "@/lib/auth";
import { visibleWorkbenchModules } from "@/lib/workbench-modules";
import { cn } from "@/lib/utils";

export default function WorkbenchPage() {
  const router = useRouter();
  const modules = visibleWorkbenchModules(getUser()?.role ?? "");

  return (
    <div className="h-full overflow-y-auto bg-muted/20">
      <div className="mx-auto max-w-6xl px-6 py-7">
        <PageHeader
          title="工作台"
          description="选择要处理的业务。每个模块独立运行，不需要先发起 AI 对话。"
        />

        <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {modules.map((module) => {
            const Icon = module.icon;
            const enabled = module.status === "available" && module.href;
            return (
              <Card
                key={module.key}
                className={cn(
                  "border-border/70 shadow-sm transition-colors",
                  enabled && "cursor-pointer hover:border-primary/40 hover:bg-background",
                  !enabled && "bg-muted/30",
                )}
                onClick={() => enabled && router.push(module.href!)}
              >
                <CardContent className="flex min-h-44 flex-col p-5">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-md border bg-background">
                      <Icon className="h-5 w-5 text-foreground/75" />
                    </div>
                    {module.status === "planned" && <Badge variant="secondary">准备中</Badge>}
                  </div>
                  <h2 className="mt-5 text-base font-semibold">{module.title}</h2>
                  <p className="mt-2 flex-1 text-sm leading-6 text-muted-foreground">
                    {module.description}
                  </p>
                  {enabled && (
                    <div className="mt-4 flex items-center gap-1 text-xs font-medium text-primary">
                      打开模块 <ArrowRight className="h-3.5 w-3.5" />
                    </div>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>

        {modules.length === 0 && (
          <div className="mt-8 flex items-center gap-2 rounded-md border bg-background p-5 text-sm text-muted-foreground">
            <BriefcaseBusiness className="h-4 w-4" /> 暂无可用模块
          </div>
        )}
      </div>
    </div>
  );
}
