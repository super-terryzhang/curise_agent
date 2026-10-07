"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { listFields } from "@/lib/data-tables-api";
import { listCountries, listPorts, listSuppliers, listCategories } from "@/lib/data-api";
import { TYPE_LABELS } from "@/lib/data-tables-view";

const CORE = [
  ["产品代码", "必填", "文本；与港口一起识别产品，例如 P-001"],
  ["港口", "必填", "从已配置的港口下拉选择"],
  ["产品名称", "新产品必填", "填写商品名称；更新已有产品时留空保留原值"],
  ["供应商", "选填", "从已配置的供应商下拉选择"],
  ["单位", "选填", "供应商供货单位，例如 KG、CT"],
  ["商品分类", "选填", "从已配置的商品分类下拉选择"],
  ["品牌", "选填", "填写品牌名称"],
  ["状态", "新产品必填", "选择启用或停用"],
  ["国家", "新产品必填", "从已配置的国家下拉选择"],
];

export function TemplateGuide() {
  const [extras, setExtras] = useState<string[][]>([]);
  const [choices, setChoices] = useState<{ label: string; names: string[] }[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    Promise.all([listFields("025588dd-ae63-5607-9e78-1179a500ed6e"), listCountries(), listPorts(), listSuppliers(), listCategories()]).then(([fields, countries, ports, suppliers, categories]) => {
      if (!active) return;
      setExtras(fields.filter(field => field.source === "extension" && field.status === "active").map(field => [field.label, field.required ? "必填" : "选填", `${TYPE_LABELS[field.field_type]}${field.config.options ? "：" + field.config.options.filter(option => option.active).map(option => option.label).join("、") : "；按配置格式填写"}${field.field_type === "multi_select" ? "；多项用中文分号分隔" : ""}`]));
      setChoices([["国家", countries], ["港口", ports], ["供应商", suppliers], ["商品分类", categories]].map(([label, values]) => ({ label: String(label), names: (values as typeof countries).filter(item => item.status === true).map(item => item.name) })));
    }).catch(reason => { if (active) setError(reason instanceof Error ? reason.message : "无法读取选项，请刷新"); });
    return () => { active = false; };
  }, []);
  return <div className="space-y-4 rounded-md border bg-background p-4 text-sm">
    <div><h3 className="font-semibold">如何填写 Excel</h3><p className="mt-2 text-muted-foreground">在对应工作表的第 5 行开始填写，表头不要修改。可以只填写产品资料、只填写价格记录，或两者一起上传；不用的工作表保留空白。</p></div>
    {error && <p role="alert" className="text-destructive">{error}</p>}
    <details><summary className="cursor-pointer font-medium">查看可选值与准备状态</summary><div className="mt-3 space-y-2">{choices.map(choice => <div key={choice.label}><span className="font-medium">{choice.label}：</span>{choice.names.length ? choice.names.join("、") : <span className={choice.label === "国家" || choice.label === "港口" ? "text-amber-700" : "text-muted-foreground"}>{choice.label === "国家" || choice.label === "港口" ? "尚未配置，新增产品前请先添加" : "尚未配置，可暂时留空"}</span>}</div>)}<Link href="/prepare/options" className="inline-block text-primary underline">维护上传选项 → 保存后重新下载模板</Link></div></details>
    <details><summary className="cursor-pointer font-medium">产品资料：一个产品填一行</summary><table className="mt-3 w-full text-left text-xs"><thead><tr className="border-b"><th className="py-2">字段</th><th>是否必填</th><th>如何填写</th></tr></thead><tbody>{[...CORE, ...extras].map(([label, required, hint]) => <tr key={label} className="border-b"><td className="py-2 pr-4">{label}</td><td className="pr-4">{required}</td><td>{hint}</td></tr>)}</tbody></table></details>
    <details><summary className="cursor-pointer font-medium">价格记录：一个价格区间填一行</summary><p className="mt-3 text-xs leading-6">产品代码、港口：填写与产品资料相同的值。价格类型：选择采购价或卖价。价格：填写数字。币种：三个大写字母，例如 JPY。开始／结束日期：填写 YYYY-MM-DD，例如 2027-01-01 至 2027-01-31。</p><p className="mt-2 text-xs text-muted-foreground">同产品同类型的区间不能重叠；起止日期完全相同会更新该期间的价格。产品可以有多个期间，采购价与卖价分开检查。只上传资料而未填价格时，显示提醒，仍可确认导入。</p></details>
  </div>;
}
