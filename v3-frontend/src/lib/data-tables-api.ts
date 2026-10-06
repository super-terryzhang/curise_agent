import { fetchWithAuth } from "./fetch-with-auth";
import type { DataTable, TableCreate, TableUpdate, DataField, FieldCreate, FieldUpdate, SchemaAction, DataRecord, RecordCreate, RecordUpdate, RecordAction, RecordQuery, DataChange, Page, Issue, Status } from "./data-tables-types";
const BASE = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001") + "/api/data-tables";
export class DataTablesApiError extends Error {
  constructor(public status:number, public code:string, message:string, public issues:Issue[]=[], public uncertain=false) { super(message); this.name="DataTablesApiError"; }
}
async function request<T>(path:string, method="GET", body?:unknown):Promise<T> {
  let res:Response;
  try { res = await fetchWithAuth(BASE+path, {method, ...(body === undefined ? {} : {headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})}); }
  catch { throw new DataTablesApiError(0,"NETWORK_ERROR", method === "GET" ? "无法加载，请重试" : "保存结果暂时无法确认，请先核对，勿创建重复记录",[],method!=="GET"); }
  if (!res.ok) {
    const data = await res.json().catch(()=>null);
    const d = data?.detail;
    throw new DataTablesApiError(res.status,d?.code||"REQUEST_ERROR",d?.message||"请求未完成，请保留输入并重试",d?.issues||[],res.status>=500&&method!=="GET");
  }
  try { return await res.json(); }
  catch { throw new DataTablesApiError(0,"RESPONSE_ERROR","结果暂时无法确认，请先核对保存结果",[],method!=="GET"); }
}
function query(input:Record<string,unknown>):string {
  const p = new URLSearchParams();
  for(const [k,v] of Object.entries(input)) if(v !== undefined && v !== null) p.set(k,typeof v === "object" ? JSON.stringify(v) : String(v));
  return "?"+p;
}
export const listTables = (q:{status?:Status;page?:number;page_size?:number}={}) => request<Page<DataTable>>(query(q));
export const createTable = (b:TableCreate) => request<DataTable>("","POST",b);
export const getTable = (t:string) => request<DataTable>(`/${t}`);
export const updateTable = (t:string,b:TableUpdate) => request<DataTable>(`/${t}`,"PATCH",b);
export const listFields = (t:string) => request<DataField[]>(`/${t}/fields`);
export const createField = (t:string,b:FieldCreate) => request<DataField>(`/${t}/fields`,"POST",b);
export const updateField = (t:string,f:string,b:FieldUpdate) => request<DataField>(`/${t}/fields/${f}`,"PATCH",b);
export const reorderFields = (t:string,b:SchemaAction&{field_ids:string[]}) => request<DataField[]>(`/${t}/fields/reorder`,"POST",b);
export const listRecords = (t:string,q:RecordQuery={}) => request<Page<DataRecord>>(`/${t}/records`+query({...q}));
export const getRecord = (t:string,r:string) => request<DataRecord>(`/${t}/records/${r}`);
export const createRecord = (t:string,b:RecordCreate) => request<DataRecord>(`/${t}/records`,"POST",b);
export const updateRecord = (t:string,r:string,b:RecordUpdate) => request<DataRecord>(`/${t}/records/${r}`,"PATCH",b);
export const archiveTable = (t:string,b:SchemaAction) => request<DataTable>(`/${t}/archive`,"POST",b);
export const restoreTable = (t:string,b:SchemaAction) => request<DataTable>(`/${t}/restore`,"POST",b);
export const archiveField = (t:string,f:string,b:SchemaAction) => request<DataField>(`/${t}/fields/${f}/archive`,"POST",b);
export const restoreField = (t:string,f:string,b:SchemaAction) => request<DataField>(`/${t}/fields/${f}/restore`,"POST",b);
export const archiveRecord = (t:string,r:string,b:RecordAction) => request<DataRecord>(`/${t}/records/${r}/archive`,"POST",b);
export const restoreRecord = (t:string,r:string,b:RecordAction) => request<DataRecord>(`/${t}/records/${r}/restore`,"POST",b);
export const listChanges = (t:string,q:{page?:number;page_size?:number;entity_type?:string;record_id?:string}={}) => request<Page<DataChange>>(`/${t}/changes`+query(q));
export const searchLinkTargets = (t:string,f:string,q:{q?:string;page?:number;page_size?:number}={}) => request<Page<DataRecord>>(`/${t}/fields/${f}/targets`+query(q));
