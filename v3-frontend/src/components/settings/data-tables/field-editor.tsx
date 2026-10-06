"use client";
import { useEffect,useRef,useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import * as api from "@/lib/data-tables-api";
import type { DataField,DataTable,FieldType,SelectOption,FieldCreate,FieldConfig,Value } from "@/lib/data-tables-types";
import { TYPE_LABELS,fromDateTimeInput,toDateTimeInput } from "@/lib/data-tables-view";
import { ErrorNotice,SELECT_CLASS,Pager } from "./shared";

export function FieldEditor({table,field,onSaved,onCancel}:{table:DataTable;field?:DataField;onSaved:()=>void;onCancel:()=>void}) {
  const [id]=useState(()=>field?.id||crypto.randomUUID());
  const [label,setLabel]=useState(field?.label||""),[kind,setKind]=useState<FieldType>(field?.field_type||"text");
  const [required,setRequired]=useState(field?.required||false),[unique,setUnique]=useState(field?.unique||false);
  const [defaultValue,setDefault]=useState<Value>(field?.default_value??null);
  const [maxLength,setMaxLength]=useState(String(field?.config.max_length??4096)),[multiline,setMultiline]=useState(field?.config.multiline||false);
  const [precision,setPrecision]=useState(String(field?.config.precision??18)),[scale,setScale]=useState(String(field?.config.scale??6));
  const [options,setOptions]=useState<SelectOption[]>(field?.config.options||[]),[target,setTarget]=useState(field?.target_table_id||"");
  const [tables,setTables]=useState<DataTable[]>([]),[targetPage,setTargetPage]=useState(1),[targetTotal,setTargetTotal]=useState(0);
  const [busy,setBusy]=useState(false),[error,setError]=useState<unknown>();
  const pending=useRef<FieldCreate|null>(null);
  useEffect(()=>{if(kind!=="link")return;let live=true;api.listTables({page:targetPage}).then(d=>{if(live){setTables(d.items);setTargetTotal(d.total);}}).catch(e=>{if(live)setError(e);});return()=>{live=false;};},[kind,targetPage]);
  const frozen=!!field&&table.record_count>0;
  const uncertain=error instanceof api.DataTablesApiError&&error.uncertain;
  function changeKind(value:FieldType) {setKind(value);setDefault(null);setUnique(false);setTarget("");}
  async function save() {
    setBusy(true);setError(null);
    const config:FieldConfig=kind==="text"?{max_length:parseInt(maxLength,10),multiline}:kind==="number"?{precision:parseInt(precision,10),scale:parseInt(scale,10)}:kind==="single_select"||kind==="multi_select"?{options}:{};
    const body:FieldCreate={id,label,field_type:kind,required,unique:kind==="text"||kind==="number"?unique:false,default_value:kind==="link"?null:defaultValue,config,target_table_id:kind==="link"?target||null:null,expected_schema_version:table.schema_version};
    try {
      if(field){const {id:_id,...patch}=body;await api.updateField(table.id,field.id,patch);}
      else{pending.current ||= body;await api.createField(table.id,pending.current);}
      onSaved();
    }catch(e){if(!(e instanceof api.DataTablesApiError&&e.uncertain))pending.current=null;setError(e);}finally{setBusy(false);}
  }
  return <form className="border rounded-lg p-4 space-y-4 max-w-3xl" onSubmit={e=>{e.preventDefault();void save();}}>
    <h2 className="text-sm font-medium">{field?"修改字段":"新增字段"}</h2><ErrorNotice error={error}/>
    <fieldset disabled={busy||uncertain} className="space-y-3">
    <label className="block text-sm space-y-1">字段名称<Input value={label} maxLength={100} onChange={e=>setLabel(e.target.value)}/></label>
    <label className="block text-sm space-y-1">字段类型<select className={SELECT_CLASS+" block w-full"} value={kind} disabled={frozen} onChange={e=>changeKind(e.target.value as FieldType)}>{Object.entries(TYPE_LABELS).map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label>
    {frozen&&<p className="text-xs text-muted-foreground">已有记录，不可直接改变类型或关联目标；请新建字段并整理数据。归档记录也受后端保护。</p>}
    <div className="flex gap-5 text-sm"><label><input type="checkbox" checked={required} onChange={e=>setRequired(e.target.checked)}/> 必填</label>{(kind==="text"||kind==="number")&&<label><input type="checkbox" checked={unique} onChange={e=>setUnique(e.target.checked)}/> 唯一</label>}</div>
    <p className="text-xs text-muted-foreground">设置必填或唯一将检查现有数据；不会自动填充旧记录。默认值只用于新记录。</p>
    {kind==="text"&&<div className="flex gap-4"><label className="text-sm">最长字符<Input type="number" min={1} max={4096} value={maxLength} onChange={e=>setMaxLength(e.target.value)}/></label><label className="text-sm"><input type="checkbox" checked={multiline} onChange={e=>setMultiline(e.target.checked)}/> 多行文本</label></div>}
    {kind==="number"&&<div className="flex gap-4"><label className="text-sm">总位数<Input type="number" min={1} max={18} value={precision} onChange={e=>setPrecision(e.target.value)}/></label><label className="text-sm">小数位数<Input type="number" min={0} max={6} value={scale} onChange={e=>setScale(e.target.value)}/></label></div>}
    {(kind==="single_select"||kind==="multi_select")&&<div className="space-y-2"><p className="text-sm">可选值（固定编号；已有选项可改名或停用）</p>{options.map(o=><div key={o.id} className="flex gap-3 items-center"><Input aria-label={`选项名称 ${o.id}`} value={o.label} onChange={e=>setOptions(options.map(x=>x.id===o.id?{...x,label:e.target.value}:x))}/><label className="text-xs whitespace-nowrap"><input type="checkbox" checked={o.active} onChange={e=>setOptions(options.map(x=>x.id===o.id?{...x,active:e.target.checked}:x))}/>启用</label></div>)}<Button variant="outline" type="button" disabled={options.length>=100} onClick={()=>setOptions([...options,{id:crypto.randomUUID(),label:"",active:true}])}>添加选项</Button></div>}
    {kind==="link"&&<div className="space-y-2"><label className="block text-sm">目标表<select className={SELECT_CLASS+" block w-full"} value={target} disabled={frozen} onChange={e=>setTarget(e.target.value)}><option value="">请选择启用的自定义表</option>{target&&!tables.some(t=>t.id===target)&&<option value={target}>当前目标 · {target}</option>}{tables.map(t=><option key={t.id} value={t.id}>{t.name} · {t.id}</option>)}</select></label><Pager page={targetPage} total={targetTotal} onPage={setTargetPage}/></div>}
    {kind!=="link"&&<label className="block text-sm space-y-1">默认值
      {kind==="boolean"?<select className={SELECT_CLASS+" block"} value={defaultValue===null?"":String(defaultValue)} onChange={e=>setDefault(e.target.value===""?null:e.target.value==="true")}><option value="">不设置</option><option value="true">是</option><option value="false">否</option></select>
      :kind==="single_select"?<select className={SELECT_CLASS+" block w-full"} value={String(defaultValue??"")} onChange={e=>setDefault(e.target.value||null)}><option value="">不设置</option>{options.filter(o=>o.active).map(o=><option key={o.id} value={o.id}>{o.label}</option>)}</select>
      :kind==="multi_select"?<span className="block space-x-3">{options.filter(o=>o.active).map(o=><span key={o.id}><input aria-label={`默认选项 ${o.label||o.id}`} type="checkbox" checked={Array.isArray(defaultValue)&&defaultValue.includes(o.id)} onChange={e=>setDefault(e.target.checked?[...(Array.isArray(defaultValue)?defaultValue:[]),o.id]:(Array.isArray(defaultValue)?defaultValue:[]).filter(v=>v!==o.id))}/>{o.label}</span>)}</span>
      :kind==="text"&&multiline?<Textarea value={String(defaultValue??"")} onChange={e=>setDefault(e.target.value||null)}/>
      :<Input type={kind==="date"?"date":kind==="datetime"?"datetime-local":"text"} step={kind==="datetime"?1:undefined} inputMode={kind==="number"?"decimal":undefined} value={kind==="datetime"?toDateTimeInput(String(defaultValue??"")):String(defaultValue??"")} onChange={e=>setDefault(kind==="datetime"?fromDateTimeInput(e.target.value):e.target.value||null)}/>}
    </label>}
    </fieldset>
    {uncertain&&<p className="text-xs">请核对字段列表；重试创建将使用原编号和原内容，不会生成第二列。更新请刷新核对后再编辑。</p>}
    <div className="flex gap-2"><Button type="submit" disabled={busy||!label.trim()||(uncertain&&!!field)}>保存字段</Button><Button type="button" variant="outline" disabled={busy} onClick={onCancel}>取消</Button></div>
  </form>;
}
