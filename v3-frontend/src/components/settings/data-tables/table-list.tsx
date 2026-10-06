"use client";
import { useCallback,useEffect,useRef,useState } from "react";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { getUser } from "@/lib/auth";
import * as api from "@/lib/data-tables-api";
import type { DataTable,Page,Status,TableCreate } from "@/lib/data-tables-types";
import { canManageRecords,canManageStructure,formatTime } from "@/lib/data-tables-view";
import { CELL,SELECT_CLASS,ErrorNotice,Pager } from "./shared";

export function TableList() {
  const role=getUser()?.role, allowed=canManageRecords(role), admin=canManageStructure(role);
  const [status,setStatus]=useState<Status>("active"),[page,setPage]=useState(1);
  const [data,setData]=useState<Page<DataTable>>(),[error,setError]=useState<unknown>(),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true);
  const [form,setForm]=useState<{id:string;name:string;description:string;original?:DataTable}|null>(null);
  const [confirmation,setConfirmation]=useState<DataTable|null>(null);
  const pending=useRef<TableCreate|null>(null), sequence=useRef(0);
  const load=useCallback(async()=>{ const n=++sequence.current; setLoading(true); setError(null); try {const d=await api.listTables({status,page}); if(n===sequence.current)setData(d);} catch(e){if(n===sequence.current)setError(e);} finally {if(n===sequence.current)setLoading(false);} },[status,page]);
  useEffect(()=>{if(allowed)void load();return()=>{sequence.current++;};},[allowed,load]);
  async function save() {
    if(!form)return; setBusy(true);setError(null);
    try {
      if(form.original) await api.updateTable(form.id,{name:form.name,description:form.description||null,expected_schema_version:form.original.schema_version});
      else { pending.current ||= {id:form.id,name:form.name,description:form.description||null}; await api.createTable(pending.current); }
      pending.current=null;setForm(null);await load();
    } catch(e){if(!(e instanceof api.DataTablesApiError&&e.uncertain))pending.current=null;setError(e);} finally{setBusy(false);}
  }
  async function changeStatus() {
    if(!confirmation)return;setBusy(true);setError(null);
    try {await (confirmation.status==="active"?api.archiveTable:api.restoreTable)(confirmation.id,{expected_schema_version:confirmation.schema_version});setConfirmation(null);await load();}catch(e){setError(e);}finally{setBusy(false);}
  }
  const uncertain=error instanceof api.DataTablesApiError&&error.uncertain;
  if(!allowed)return <div className="p-6"><PageHeader title="自定义数据表"/><p className="mt-4 text-sm">当前角色无权访问自定义数据表。</p></div>;
  return <div className="p-6 space-y-5 h-full overflow-auto">
    <a className="text-xs text-muted-foreground underline" href="/dashboard/settings">返回设置中心</a>
    <PageHeader title="自定义数据表" description="创建业务表、配置列并维护记录；公司授权角色共享访问。" action={admin&&<Button onClick={()=>{setError(null);pending.current=null;setForm({id:crypto.randomUUID(),name:"",description:""});}}>新建表</Button>}/>
    <p className="text-xs text-muted-foreground">独立于订单提取字段；不会改变产品、价格、PO 匹配或询价逻辑。</p>
    <ErrorNotice error={error}/>
    {form&&<form className="rounded-lg border p-4 space-y-3 max-w-2xl" onSubmit={e=>{e.preventDefault();void save();}}>
      <h2 className="text-sm font-medium">{form.original?"修改表信息":"新建表"}</h2>
      <label className="block text-sm space-y-1">表名<Input value={form.name} disabled={busy||uncertain} maxLength={100} onChange={e=>setForm({...form,name:e.target.value})}/></label>
      <label className="block text-sm space-y-1">说明<Textarea value={form.description} disabled={busy||uncertain} maxLength={2000} onChange={e=>setForm({...form,description:e.target.value})}/></label>
      <div className="flex gap-2"><Button disabled={busy||!form.name.trim()} type="submit">{uncertain?"使用原请求核对／重试":"保存"}</Button><Button type="button" variant="outline" disabled={busy} onClick={()=>{setForm(null);pending.current=null;setError(null);}}>取消</Button></div>
      {uncertain&&<p className="text-xs">当前重试保留原请求编号和内容。请先核对列表；不自动新增第二张表。</p>}
    </form>}
    {confirmation&&<div role="dialog" aria-label="确认表状态" className="border rounded-lg p-4 space-y-3"><p className="text-sm">{confirmation.status==="active"?"归档":"恢复"}“{confirmation.name}”？归档保留字段、数据和历史，不会物理删除。</p><Button disabled={busy} onClick={()=>void changeStatus()}>确认{confirmation.status==="active"?"归档":"恢复"}</Button> <Button variant="outline" disabled={busy} onClick={()=>setConfirmation(null)}>取消</Button></div>}
    <div className="flex gap-2 items-center"><label className="text-sm">状态 <select className={SELECT_CLASS} value={status} onChange={e=>{setStatus(e.target.value as Status);setPage(1);setData(undefined);}}><option value="active">启用</option><option value="archived">归档</option></select></label><Button variant="outline" disabled={loading||busy} onClick={()=>void load()}>刷新</Button></div>
    {loading?<p className="text-sm" role="status">正在加载…</p>:data&&<><div className="rounded-lg border overflow-x-auto"><table className="w-full text-sm"><thead className="bg-muted/40"><tr>{["表名","状态","启用字段","启用记录","结构更新时间（日本）","操作"].map(h=><th className={CELL} key={h}>{h}</th>)}</tr></thead><tbody>{data.items.map(t=><tr key={t.id}><td className={CELL}><a className="underline" href={`/dashboard/settings/data-tables/${t.id}`}>{t.name}</a>{t.description&&<p className="text-xs text-muted-foreground mt-1">{t.description}</p>}</td><td className={CELL}>{t.status==="active"?"启用":"归档"}</td><td className={CELL}>{t.field_count}</td><td className={CELL}>{t.record_count}</td><td className={CELL}>{formatTime(t.updated_at)}</td><td className={CELL}><div className="flex gap-2"><a className="underline" href={`/dashboard/settings/data-tables/${t.id}`}>打开</a>{admin&&<><Button size="xs" variant="outline" disabled={busy||!!form||!!confirmation} onClick={()=>{setError(null);setForm({id:t.id,name:t.name,description:t.description||"",original:t});}}>修改表信息</Button><Button size="xs" variant="outline" disabled={busy||!!confirmation} onClick={()=>setConfirmation(t)}>{t.status==="active"?"归档":"恢复"}</Button></>}</div></td></tr>)}</tbody></table>{!data.items.length&&<p className="p-6 text-sm text-muted-foreground">暂无{status==="active"?"启用":"归档"}表{admin&&status==="active"?"，请先新建表，再配置字段。":"。"}</p>}</div><Pager page={page} total={data.total} onPage={setPage} disabled={busy}/></>}
  </div>;
}
