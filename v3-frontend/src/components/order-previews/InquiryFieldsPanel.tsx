"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, ChevronDown, ChevronRight, Save, Info } from "lucide-react";
import { toast } from "sonner";

import {
  getInquiryFields,
  patchInquiryFields,
  type InquiryField,
  type InquiryFieldSource,
  type InquiryFieldsResponse,
} from "@/lib/orders-api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";

interface Props {
  orderId: number;
  supplierId: number;
  /** Template currently selected in the picker (may differ from the
   *  inquiry's bound template until regenerate). Drives the field-list
   *  shape so switching templates updates the panel instantly. */
  templateId?: number | null;
  /** Bumped by parent after a regenerate so the panel refetches. */
  refreshKey?: number;
  /** Called after a successful save so the parent can refresh the order. */
  onSaved?: () => void;
}

const SOURCE_LABEL: Record<InquiryFieldSource, string> = {
  metadata: "来自 PDF / 已修改",
  port_master: "来自港口主数据",
  supplier_master: "来自供应商主数据",
  empty: "空 — 请填写",
};

const SOURCE_CLASS: Record<InquiryFieldSource, string> = {
  metadata: "bg-blue-50 text-blue-700 border-blue-200",
  port_master: "bg-teal-50 text-teal-700 border-teal-200",
  supplier_master: "bg-green-50 text-green-700 border-green-200",
  empty: "bg-gray-100 text-gray-600 border-gray-200",
};

export default function InquiryFieldsPanel({
  orderId,
  supplierId,
  templateId,
  refreshKey,
  onSaved,
}: Props) {
  const [data, setData] = useState<InquiryFieldsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [filledOpen, setFilledOpen] = useState(false);

  const refetch = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await getInquiryFields(orderId, supplierId, templateId);
      setData(resp);
      setEdits({});
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "字段加载失败");
    } finally {
      setLoading(false);
    }
  }, [orderId, supplierId, templateId]);

  const dirtyRef = useRef(false);
  dirtyRef.current = Object.keys(edits).length > 0;

  // When templateId changes, refetch — but warn first if the user has
  // unsaved edits. We hold the warning out of the refetch's useEffect
  // dependency array so it only fires when the *input* changes, not when
  // refetch is re-bound.
  useEffect(() => {
    if (dirtyRef.current) {
      const ok = window.confirm(
        "切换模板会丢失当前未保存的修改，是否继续？",
      );
      if (!ok) return;
    }
    refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderId, supplierId, templateId, refreshKey]);

  const { emptyFields, filledFields } = useMemo(() => {
    const fields = data?.fields ?? [];
    return {
      emptyFields: fields.filter((f) => f.source === "empty"),
      filledFields: fields.filter((f) => f.source !== "empty"),
    };
  }, [data]);

  const dirty = Object.keys(edits).length > 0;

  function handleEdit(key: string, current: string | null, next: string) {
    // Only treat as dirty if the value actually differs from current.
    const cur = current ?? "";
    if (next === cur) {
      // Remove from edits if user reverts.
      setEdits((prev) => {
        const { [key]: _, ...rest } = prev;
        return rest;
      });
    } else {
      setEdits((prev) => ({ ...prev, [key]: next }));
    }
  }

  async function handleSave() {
    if (!dirty) return;
    setSaving(true);
    try {
      const result = await patchInquiryFields(orderId, supplierId, edits);
      toast.success(
        `已保存 ${Object.keys(edits).length} 个字段 — 请点击「重做」重新生成 Excel`,
        { duration: 5000 },
      );
      // Surface where each edit landed in dev/debug logs without spamming the user.
      if (result.saved_supplier_keys.length && result.saved_order_keys.length) {
        // mixed save — nothing extra to do, both groups will refresh on refetch
      }
      onSaved?.();
      await refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-6 text-xs text-muted-foreground">
        <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
        加载字段映射中…
      </div>
    );
  }

  if (!data || data.reason) {
    return (
      <div className="rounded-md border border-dashed border-muted-foreground/30 p-4 text-xs text-muted-foreground">
        <Info className="inline mr-1.5 h-3.5 w-3.5" />
        {data?.reason || "暂无字段映射"}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-xs text-muted-foreground">
          <span className="font-medium text-foreground">字段映射</span> ·
          模板{" "}
          <span className="font-medium">
            {data.template?.name ?? "—"}
          </span>{" "}
          · 共 {(data.fields ?? []).length} 项 ·{" "}
          <span className="text-amber-700">{emptyFields.length} 项待填</span>
          {dirty && (
            <span className="ml-2 text-orange-600">
              · 有未保存改动
            </span>
          )}
        </div>
        <Button
          variant="default"
          size="sm"
          className="text-xs h-7"
          disabled={!dirty || saving}
          onClick={handleSave}
        >
          {saving ? (
            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
          ) : (
            <Save className="mr-1 h-3 w-3" />
          )}
          保存
        </Button>
      </div>

      {/* Group 1: empty fields, always open, prominently styled */}
      {emptyFields.length > 0 && (
        <div className="rounded-md border border-amber-200 bg-amber-50/40">
          <div className="px-3 py-2 text-xs font-medium text-amber-800 border-b border-amber-200">
            待填字段（{emptyFields.length}）
          </div>
          <div className="divide-y divide-amber-100">
            {emptyFields.map((f) => (
              <FieldRow
                key={f.key}
                field={f}
                editValue={edits[f.key]}
                onChange={(v) => handleEdit(f.key, f.value, v)}
              />
            ))}
          </div>
        </div>
      )}

      {/* Group 2: filled fields, collapsible */}
      {filledFields.length > 0 && (
        <div className="rounded-md border border-muted-foreground/20">
          <button
            type="button"
            onClick={() => setFilledOpen((v) => !v)}
            className="w-full px-3 py-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:bg-muted/30"
          >
            {filledOpen ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
            已填字段（{filledFields.length}） — 点击展开{filledOpen ? "" : "可修改"}
          </button>
          {filledOpen && (
            <div className="divide-y divide-muted-foreground/10 border-t border-muted-foreground/20">
              {filledFields.map((f) => (
                <FieldRow
                  key={f.key}
                  field={f}
                  editValue={edits[f.key]}
                  onChange={(v) => handleEdit(f.key, f.value, v)}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Fields that often carry multi-line content (delivery_address shipped from
// Port master includes line breaks like "〒xxx\nshipping prefecture\nberth").
// Single-line inputs collapse these to one row, which both reads poorly and
// surprises the user when their typed newline silently disappears.
const MULTILINE_FIELD_KEYS = new Set([
  "delivery_address",
  "supplier_address",
  "delivery_time_notes",
  "delivery_contact",
]);

function FieldRow({
  field,
  editValue,
  onChange,
}: {
  field: InquiryField;
  editValue: string | undefined;
  onChange: (v: string) => void;
}) {
  const displayed = editValue ?? field.value ?? "";
  const isMultiline =
    MULTILINE_FIELD_KEYS.has(field.key) || (field.value ?? "").includes("\n");
  return (
    <div className="px-3 py-2 grid grid-cols-[180px_1fr_140px] gap-3 items-start">
      <div className="text-xs pt-1">
        <div className="font-medium">{field.label}</div>
        <div className="text-muted-foreground font-mono text-[10px]">
          {field.key} · {field.position || "—"}
        </div>
      </div>
      {isMultiline ? (
        <Textarea
          className="text-xs min-h-[56px] resize-y"
          value={displayed}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.source === "empty" ? "未填写" : ""}
        />
      ) : (
        <Input
          className="h-7 text-xs"
          value={displayed}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.source === "empty" ? "未填写" : ""}
        />
      )}
      <div className="flex justify-end pt-1">
        <Badge
          variant="outline"
          className={`text-[10px] py-0 px-1.5 ${SOURCE_CLASS[field.source]}`}
        >
          {SOURCE_LABEL[field.source]}
        </Badge>
      </div>
    </div>
  );
}
