"use client";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { getUser } from "@/lib/auth";
import * as api from "@/lib/data-tables-api";
import type { DataTable,DataField } from "@/lib/data-tables-types";
import { TYPE_LABELS,canManageStructure,displayValue } from "@/lib/data-tables-view";
import { FieldEditor } from "./field-editor";
import { ErrorNotice,SELECT_CLASS,CELL } from "./shared";

export function FieldsPanel({table,fields,onChanged}:{table:DataTable;fields:DataField[];onChanged:()=>void}) {
  const admin=canManageStructure(getUser()?.role),editable=admin&&table.status==="active";
  const [editor,setEditor]=useState<DataField|"new"|null>(null),[confirmation,setConfirmation]=useState<DataField|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState<unknown>();
  async function action(run:()=>Promise<unknown>) {setBusy(true);setError(null);try{await run();setConfirmation(null);onChanged();}catch(e){setError(e);}finally{setBusy(false);}}
  const active=fields.filter(f=>f.status==="active");
  function move(f:DataField,direction:number) {const ids=active.map(x=>x.id),a=ids.indexOf(f.id),b=a+direction;[ids[a],ids[b]]=[ids[b],ids[a]];void action(()=>api.reorderFields(table.id,{expected_schema_version:table.schema_version,field_ids:ids}));}
  return <div className="space-y-4">
    <ErrorNotice error={error}/>
    <div className="flex flex-wrap items-center justify-between gap-3"><label className="text-sm">记录显示名称 <select className={SELECT_CLASS} disabled={!editable||busy||!!editor} value={table.display_field_id||""} onChange={e=>void action(()=>api.updateTable(table.id,{expected_schema_version:table.schema_version,display_field_id:e.target.value||null}))}><option value="">使用记录编号</option>{active.filter(f=>f.field_type==="text").map(f=><option key={f.id} value={f.id}>{f.label}</option>)}</select></label>{editable&&<Button disabled={busy||!!editor} onClick={()=>setEditor("new")}>新增字段</Button>}</div>
    {!admin&&<p className="text-xs text-muted-foreground">当前角色可查看配置；表和字段由管理员维护。</p>}
    {editor&&<FieldEditor key={editor==="new"?"new":editor.id} table={table} field={editor==="new"?undefined:editor} onSaved={()=>{setEditor(null);onChanged();}} onCancel={()=>setEditor(null)}/>}
    {confirmation&&<div role="dialog" aria-label="确认字段状态" className="border rounded-lg p-4 space-y-3"><p className="text-sm">{confirmation.status==="active"?"归档":"恢复"}“{confirmation.label}”？原值、关联与历史均保留。</p><Button disabled={busy} onClick={()=>void action(()=>(confirmation.status==="active"?api.archiveField:api.restoreField)(table.id,confirmation.id,{expected_schema_version:table.schema_version}))}>确认{confirmation.status==="active"?"归档":"恢复"}</Button> <Button variant="outline" disabled={busy} onClick={()=>setConfirmation(null)}>取消</Button></div>}
    <div className="border rounded-lg overflow-x-auto"><table className="w-full text-sm"><thead className="bg-muted/40"><tr>{["字段名称","类型","必填","唯一","默认值","状态","操作"].map(h=><th className={CELL} key={h}>{h}</th>)}</tr></thead><tbody>{fields.map(f=><tr key={f.id} className={f.status==="archived"?"text-muted-foreground":""}><td className={CELL}>{f.label}</td><td className={CELL}>{TYPE_LABELS[f.field_type]||"未知类型"}</td><td className={CELL}>{f.required?"是":"否"}</td><td className={CELL}>{f.unique?"是":"否"}</td><td className={CELL}>{displayValue(f,f.default_value)}</td><td className={CELL}>{f.status==="active"?"启用":"归档"}</td><td className={CELL}>{editable&&<div className="flex gap-1">{f.status==="active"&&<><Button size="xs" variant="outline" disabled={busy||!!editor} onClick={()=>setEditor(f)}>修改</Button><Button size="xs" variant="outline" aria-label={`上移 ${f.label}`} disabled={busy||!!editor||active.indexOf(f)===0} onClick={()=>move(f,-1)}>↑</Button><Button size="xs" variant="outline" aria-label={`下移 ${f.label}`} disabled={busy||!!editor||active.indexOf(f)===active.length-1} onClick={()=>move(f,1)}>↓</Button></>}<Button size="xs" variant="outline" disabled={busy||!!editor||!!confirmation} onClick={()=>setConfirmation(f)}>{f.status==="active"?"归档":"恢复"}</Button></div>}</td></tr>)}</tbody></table>{!fields.length&&<p className="p-5 text-sm text-muted-foreground">尚无字段，请先配置列，再录入记录。</p>}</div>
  </div>;
}
