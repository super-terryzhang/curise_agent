"use client";

import { useState } from "react";
import CountriesTab from "@/app/dashboard/data/CountriesTab";
import PortsTab from "@/app/dashboard/data/PortsTab";
import SuppliersTab from "@/app/dashboard/data/SuppliersTab";
import CategoriesTab from "@/app/dashboard/data/CategoriesTab";
import { Button } from "@/components/ui/button";

export default function PreparationOptionsPage() {
  const [tab, setTab] = useState("countries");
  return <div className="space-y-5 p-6"><div><h1 className="text-lg font-semibold">上传选项</h1><p className="mt-2 text-sm text-muted-foreground">先建立国家和港口，再按需建立供应商与商品分类；保存后重新下载 Excel，这些名称会成为下拉选项。</p></div><nav className="flex gap-2 border-b pb-3">{[["countries", "国家"], ["ports", "港口"], ["suppliers", "供应商"], ["categories", "商品分类"]].map(([value, label]) => <Button key={value} variant={tab === value ? "secondary" : "ghost"} onClick={() => setTab(value)}>{label}</Button>)}</nav>{tab === "countries" ? <CountriesTab /> : tab === "ports" ? <PortsTab /> : tab === "suppliers" ? <SuppliersTab /> : <CategoriesTab />}</div>;
}
