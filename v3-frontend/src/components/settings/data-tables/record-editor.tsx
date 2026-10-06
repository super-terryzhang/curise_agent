"use client";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { getUser } from "@/lib/auth";
import * as api from "@/lib/data-tables-api";
import type {
  DataTable,
  DataField,
  DataRecord,
  Values,
  RecordCreate,
  SystemRecordUpdate,
} from "@/lib/data-tables-types";
import {
  canManageRecords,
  canManageStructure,
  buildRecordValues,
  fieldIssues,
} from "@/lib/data-tables-view";
import { ValueControl } from "./value-control";
import { ErrorNotice } from "./shared";

function initial(fields: DataField[], record?: DataRecord): Values {
  return record
    ? { ...record.values }
    : Object.fromEntries(
        fields
          .filter((f) => f.status === "active" && f.default_value !== null)
          .map((f) => [f.id, f.default_value]),
      );
}
export function RecordEditor({
  table,
  fields,
  record,
  onSaved,
  onCancel,
}: {
  table: DataTable;
  fields: DataField[];
  record?: DataRecord;
  onSaved: () => void;
  onCancel: () => void;
}) {
  // Capture the editing contract; parent refresh must not silently replace a draft.
  const [contract, setContract] = useState({ table, fields, record });
  const [id] = useState(() => record?.id || crypto.randomUUID());
  const [requestId] = useState(() => crypto.randomUUID());
  const [draft, setDraft] = useState<Values>(() => initial(fields, record)),
    [error, setError] = useState<unknown>(),
    [busy, setBusy] = useState(false);
  const [confirmation, setConfirmation] = useState<"cancel" | "reload" | null>(
      null,
    ),
    [check, setCheck] = useState<DataRecord | null>(null);
  const pending = useRef<RecordCreate | SystemRecordUpdate | null>(null);
  const system = contract.table.table_kind === "system";
  const coreFields = contract.fields.filter((field) => field.locked);
  const extensionFields = contract.fields.filter((field) => !field.locked);
  const activeEditableFields = (system ? extensionFields : contract.fields).filter(
    (field) => field.status === "active",
  );
  const dirty =
    JSON.stringify(draft) !==
    JSON.stringify(initial(contract.fields, contract.record));
  const writable =
    canManageRecords(getUser()?.role) &&
    contract.table.status === "active" &&
    (!contract.record || contract.record.status === "active");
  const admin = canManageStructure(getUser()?.role);
  const uncertain = error instanceof api.DataTablesApiError && error.uncertain;
  const conflict =
    error instanceof api.DataTablesApiError && error.status === 409;
  const issues = fieldIssues(
    error instanceof api.DataTablesApiError ? error : {},
  );
  const newer = table.schema_version > contract.table.schema_version;
  useEffect(() => {
    if (!dirty && !uncertain) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, uncertain]);
  async function save() {
    setBusy(true);
    setError(null);
    try {
      if (system) {
        if (!contract.record)
          throw new Error("系统数据必须从现有业务记录打开");
        pending.current ||= {
          request_id: requestId,
          source_record_id: id,
          expected_revision: contract.record.revision,
          schema_version: contract.table.schema_version,
          values: buildRecordValues(
            extensionFields,
            draft,
            contract.record.values,
          ),
        } satisfies SystemRecordUpdate;
        await api.saveSystemRecord(
          table.id,
          id,
          pending.current as SystemRecordUpdate,
        );
      } else if (contract.record)
        await api.updateRecord(table.id, id, {
          expected_revision: contract.record.revision,
          schema_version: contract.table.schema_version,
          values: buildRecordValues(
            contract.fields,
            draft,
            contract.record.values,
          ),
        });
      else {
        pending.current ||= {
          id,
          schema_version: contract.table.schema_version,
          values: buildRecordValues(contract.fields, draft),
        };
        await api.createRecord(table.id, pending.current as RecordCreate);
      }
      onSaved();
    } catch (e) {
      if (!(e instanceof api.DataTablesApiError && e.uncertain))
        pending.current = null;
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function reload() {
    setBusy(true);
    try {
      const [t, f, r] = await Promise.all([
        api.getTable(table.id),
        api.listFields(table.id),
        contract.record
          ? api.getRecord(table.id, id)
          : Promise.resolve(undefined),
      ]);
      setContract({ table: t, fields: f, record: r });
      setDraft(initial(f, r));
      pending.current = null;
      setCheck(null);
      setError(null);
      setConfirmation(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function checkResult() {
    setBusy(true);
    try {
      setCheck(await api.getRecord(table.id, id));
    } catch (e) {
      setCheck(null);
      setError(
        new api.DataTablesApiError(
          0,
          "CHECK_PENDING",
          e instanceof api.DataTablesApiError && e.status === 404
            ? "目前尚未读取到记录；原请求仍保留，请稍后核对或使用原请求重试"
            : "无法核对保存结果；原请求仍保留，请稍后核对",
          [],
          true,
        ),
      );
    } finally {
      setBusy(false);
    }
  }
  function renderField(f: DataField, core = false) {
    const controlField = core ? { ...f, label: `${f.label}（只读）` } : f;
    return (
      <div className="space-y-1 min-w-0" key={f.id}>
        {core ? (
          <p className="text-sm font-medium">
            {controlField.label}
            {f.required ? " *" : ""}
          </p>
        ) : (
          <label htmlFor={`value-${f.id}`} className="text-sm font-medium">
            {controlField.label}
            {f.required ? " *" : ""}
            {f.status === "archived" ? "（归档，只读）" : ""}
          </label>
        )}
        <ValueControl
          field={controlField}
          value={draft[f.id]}
          disabled={
            core || !writable || f.status === "archived" || uncertain
          }
          initialLabel={contract.record?.linked_labels?.[f.id]}
          onChange={(v) => setDraft((old) => ({ ...old, [f.id]: v }))}
        />
        {issues[f.id]?.map((message, index) => (
          <p key={index} className="text-xs text-destructive">
            {message}
          </p>
        ))}
        {f.field_type === "datetime" && (
          <p className="text-xs text-muted-foreground">按日本时间填写</p>
        )}
      </div>
    );
  }
  return (
    <form
      className="border rounded-lg p-4 space-y-4 max-w-4xl"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <h2 className="text-sm font-medium">
        {contract.record ? (writable ? "编辑记录" : "查看记录") : "新增记录"}
      </h2>
      <p className="text-xs text-muted-foreground break-all">
        记录编号：{id} · 结构版本 {contract.table.schema_version}
        {contract.record && ` · 记录版本 ${contract.record.revision}`}
      </p>
      {system && contract.record?.business_url && (
        <a className="text-sm underline" href={contract.record.business_url}>
          打开业务页面
        </a>
      )}
      <ErrorNotice error={error} />
      {newer && (
        <p role="alert" className="text-sm text-destructive">
          字段配置已变化，草稿已保留。请明确载入最新配置后再保存。
        </p>
      )}
      {check && (
        <div className="border rounded-md p-3 text-sm">
          <p>
            已读取当前记录，版本 {check.revision}
            ；请核对，不自动判断原保存是否完整成功。
          </p>
          <pre className="text-xs whitespace-pre-wrap break-all">
            {JSON.stringify(check.values, null, 2)}
          </pre>
        </div>
      )}
      {system ? (
        <>
          <section className="space-y-3">
            <h3 className="text-sm font-medium">核心信息</h3>
            <fieldset
              disabled
              className="grid gap-4 rounded-md border bg-muted/20 p-3 md:grid-cols-2"
            >
              {coreFields.map((field) => renderField(field, true))}
            </fieldset>
          </section>
          <section className="space-y-3">
            <h3 className="text-sm font-medium">扩展信息</h3>
            {extensionFields.length ? (
              <fieldset
                disabled={busy || uncertain}
                className="grid gap-4 md:grid-cols-2"
              >
                {extensionFields.map((field) => renderField(field))}
              </fieldset>
            ) : (
              <p className="text-sm text-muted-foreground">
                尚未配置扩展字段。
                {admin ? (
                  <>
                    请先前往字段配置页
                    <a
                      className="ml-1 underline"
                      href={`/dashboard/settings/data-tables/${table.id}?tab=fields`}
                    >
                      新增字段
                    </a>
                    。
                  </>
                ) : (
                  "当前仅可查看核心信息；扩展字段由管理员配置。"
                )}
              </p>
            )}
          </section>
        </>
      ) : (
        <fieldset
          disabled={busy || uncertain}
          className="grid gap-4 md:grid-cols-2"
        >
          {contract.fields.map((field) => renderField(field))}
        </fieldset>
      )}
      {!system && !contract.fields.some((f) => f.status === "active") && (
        <p className="text-sm">没有启用字段，请先配置字段。</p>
      )}
      {(conflict || newer || uncertain) && (
        <div className="flex flex-wrap gap-2">
          {uncertain && (
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => void checkResult()}
            >
              核对已保存记录
            </Button>
          )}
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => setConfirmation("reload")}
          >
            载入最新并重新编辑
          </Button>
        </div>
      )}
      {confirmation && (
        <div
          role="dialog"
          aria-label="未保存修改"
          className="border rounded-lg p-3 space-y-2"
        >
          <p className="text-sm">
            {confirmation === "cancel" ? "关闭表单" : "载入最新数据"}
            会放弃当前草稿。
            {uncertain && "保存结果尚未确认，关闭前请核对记录编号。"}
          </p>
          <Button
            type="button"
            disabled={busy}
            onClick={() =>
              confirmation === "cancel" ? onCancel() : void reload()
            }
          >
            {confirmation === "cancel" ? "放弃未保存修改" : "确认放弃并刷新"}
          </Button>{" "}
          <Button
            type="button"
            variant="outline"
            onClick={() => setConfirmation(null)}
          >
            继续编辑
          </Button>
        </div>
      )}
      <div className="flex gap-2">
        {writable && activeEditableFields.length > 0 && (
          <Button
            type="submit"
            disabled={
              busy ||
              conflict ||
              newer ||
              (uncertain && !!contract.record && !system) ||
              !activeEditableFields.length
            }
          >
            {uncertain
              ? "使用原请求核对／重试"
              : system
                ? "保存扩展信息"
                : "保存记录"}
          </Button>
        )}
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          onClick={() =>
            dirty || uncertain ? setConfirmation("cancel") : onCancel()
          }
        >
          {writable ? "取消" : "关闭"}
        </Button>
      </div>
    </form>
  );
}
