"use client";

/**
 * ZoneConfigEditor — edit the zone_config of an already-uploaded supplier template.
 *
 * Primary goal: let users FIX LLM analysis mistakes (e.g. Bug #1 where the
 * Japanese template's buyer block was misclassified as supplier_*).
 * Without this, the only recourse was to delete + re-upload, which is lossy
 * and doesn't necessarily give a better result.
 *
 * This editor owns the full runtime contract: header bindings and display
 * formats, dynamic row zones, product mappings/formulas, summary formulas and
 * cross-zone references. The backend validates the same contract on save.
 */

import { useState, useMemo } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ArrowLeft, Plus, Trash2, Save, AlertCircle } from "lucide-react";


// ─── Canonical data paths ─────────────────────────────────────────
// These mirror the backend FIELD_DATA_PATHS dict in zone_config_builder.py
// and the additional paths used by compose_render's _resolve_data_path.

export const HEADER_DATA_PATHS: { value: string; label: string; group: string }[] = [
  { value: "generated_date",              label: "生成日期 (generated_date)",          group: "询价单" },
  { value: "inquiry_reference",           label: "询价单号 (inquiry_reference)",       group: "询价单" },
  { value: "internal_contact",            label: "内部联系人 (internal_contact)",       group: "询价单" },
  { value: "ship_port_title",             label: "船名＋目标港口 (ship_port_title)",    group: "询价单" },
  // Order-level
  { value: "order_data.ship_name",         label: "船名 (ship_name)",                 group: "订单" },
  { value: "order_data.po_number",         label: "PO号 (po_number)",                 group: "订单" },
  { value: "order_data.order_date",        label: "下单日期 (order_date)",             group: "订单" },
  { value: "order_data.delivery_date",     label: "交货日期 (delivery_date)",           group: "订单" },
  { value: "order_data.delivery_address",  label: "交货地址 (delivery_address)",        group: "订单" },
  { value: "order_data.destination_port",  label: "目的港 (destination_port)",          group: "订单" },
  { value: "order_data.voyage",            label: "航次 (voyage)",                    group: "订单" },
  { value: "order_data.invoice_number",    label: "发票号 (invoice_number)",           group: "订单" },
  { value: "order_data.currency",          label: "币种 (currency)",                   group: "订单" },
  // Supplier-specific (contain {sid} placeholder, resolved at render)
  { value: "suppliers.{sid}.supplier_name",                             label: "供应商名称 (supplier_name)",    group: "供应商" },
  { value: "suppliers.{sid}.supplier_info.contact",                     label: "供应商联系人 (contact)",        group: "供应商" },
  { value: "suppliers.{sid}.supplier_info.phone",                       label: "供应商电话 (phone)",           group: "供应商" },
  { value: "suppliers.{sid}.supplier_info.fax",                         label: "供应商传真 (fax)",             group: "供应商" },
  { value: "suppliers.{sid}.supplier_info.email",                       label: "供应商邮箱 (email)",           group: "供应商" },
  { value: "suppliers.{sid}.supplier_info.address",                     label: "供应商地址 (address)",         group: "供应商" },
  { value: "suppliers.{sid}.supplier_info.zip_code",                    label: "供应商邮编 (zip_code)",        group: "供应商" },
  { value: "suppliers.{sid}.supplier_info.default_payment_terms",       label: "付款期限 (payment_terms)",      group: "供应商" },
  { value: "suppliers.{sid}.supplier_info.default_payment_method",      label: "付款方式 (payment_method)",     group: "供应商" },
  // Delivery location
  { value: "delivery_location.contact_person",   label: "交货联系人 (contact_person)",    group: "交货点" },
  { value: "delivery_location.delivery_notes",   label: "交货备注 (delivery_notes)",      group: "交货点" },
];


export const PRODUCT_FIELDS: { value: string; label: string }[] = [
  { value: "line_number",     label: "行号 (line_number)" },
  { value: "po_number",       label: "PO号 (po_number)" },
  { value: "product_code",    label: "产品编码 (product_code)" },
  { value: "product_name",    label: "产品名称 (product_name)" },
  { value: "product_name_en", label: "英文名 (product_name_en)" },
  { value: "product_name_jp", label: "日文名 (product_name_jp)" },
  { value: "description",     label: "描述/包装 (description)" },
  { value: "quantity",        label: "数量 (quantity)" },
  { value: "unit",            label: "单位 (unit)" },
  { value: "unit_price",      label: "单价 (unit_price)" },
  { value: "currency",        label: "币种标签 (currency)" },
];


// Shape of a zone_config object (we only type the parts we touch).
export interface ZoneConfigEditable {
  zones?: {
    product_data?: { start?: number; end?: number };
    summary?: { start?: number; end?: number };
  };
  header_fields?: Record<string, string>;
  header_formats?: Record<string, string>;
  product_columns?: Record<string, string>;
  product_row_formulas?: Record<string, string>;
  summary_formulas?: Array<Record<string, unknown>>;
  external_refs?: Array<Record<string, unknown>>;
  [k: string]: unknown;
}


// ─── Validation helpers ────────────────────────────────────────────

const CELL_REF_RE = /^[A-Z]{1,3}\d+$/;
const COL_LETTER_RE = /^[A-Z]{1,3}$/;
const SUMMARY_TYPES = ["product_sum", "relative"] as const;


// ─── The editor ────────────────────────────────────────────────────

interface Props {
  templateName: string;
  initial: ZoneConfigEditable;
  onCancel: () => void;
  onSave: (next: ZoneConfigEditable) => Promise<void>;
}

export function ZoneConfigEditor({ templateName, initial, onCancel, onSave }: Props) {
  // Flatten header_fields into an array so the user can edit per row, add/delete.
  const [headerRows, setHeaderRows] = useState<Array<{ cell: string; path: string; format: string }>>(
    () => Object.entries(initial.header_fields || {}).map(([cell, path]) => ({
      cell,
      path,
      format: initial.header_formats?.[cell] || "",
    }))
  );
  const [columnRows, setColumnRows] = useState<Array<{ col: string; field: string }>>(
    () => Object.entries(initial.product_columns || {}).map(([col, field]) => ({ col, field }))
  );
  const [saving, setSaving] = useState(false);
  const [zones, setZones] = useState(() => ({
    productStart: String(initial.zones?.product_data?.start ?? ""),
    productEnd: String(initial.zones?.product_data?.end ?? ""),
    summaryStart: String(initial.zones?.summary?.start ?? ""),
    summaryEnd: String(initial.zones?.summary?.end ?? ""),
  }));
  const [formulaRows, setFormulaRows] = useState<Array<{ col: string; formula: string }>>(
    () => Object.entries(initial.product_row_formulas || {}).map(([col, formula]) => ({ col, formula }))
  );
  const [summaryRows, setSummaryRows] = useState<Array<{ cell: string; type: string; label: string; col: string; formula: string }>>(
    () => (initial.summary_formulas || []).map((item) => ({
      cell: String(item.cell ?? ""),
      type: String(item.type ?? "relative"),
      label: String(item.label ?? ""),
      col: String(item.col ?? ""),
      formula: String(item.formula_template ?? ""),
    }))
  );
  const [externalRows, setExternalRows] = useState<Array<{ cell: string; formula: string }>>(
    () => (initial.external_refs || []).map((item) => ({
      cell: String(item.cell ?? ""),
      formula: String(item.formula_template ?? ""),
    }))
  );

  // Validation: cell refs and column letters
  const headerErrors = useMemo(() => {
    const errors: Record<number, string> = {};
    const seen = new Set<string>();
    headerRows.forEach((row, i) => {
      if (!row.cell.trim()) {
        errors[i] = "cell 不能为空";
      } else if (!CELL_REF_RE.test(row.cell.trim().toUpperCase())) {
        errors[i] = "cell 必须像 'B2' (列字母 + 行号)";
      } else if (seen.has(row.cell.trim().toUpperCase())) {
        errors[i] = "同一个 cell 不能出现两次";
      } else if (!row.path.trim()) {
        errors[i] = "数据路径不能为空";
      } else if (row.format && !row.format.includes("{value}")) {
        errors[i] = "显示格式必须包含 {value}";
      }
      seen.add(row.cell.trim().toUpperCase());
    });
    return errors;
  }, [headerRows]);

  const columnErrors = useMemo(() => {
    const errors: Record<number, string> = {};
    const seen = new Set<string>();
    columnRows.forEach((row, i) => {
      if (!row.col.trim()) {
        errors[i] = "列字母不能为空";
      } else if (!COL_LETTER_RE.test(row.col.trim().toUpperCase())) {
        errors[i] = "必须是列字母 (A, B, ..., AA)";
      } else if (seen.has(row.col.trim().toUpperCase())) {
        errors[i] = "同一列不能出现两次";
      } else if (!row.field.trim()) {
        errors[i] = "字段不能为空";
      }
      seen.add(row.col.trim().toUpperCase());
    });
    return errors;
  }, [columnRows]);

  const structureErrors = useMemo(() => {
    const errors: string[] = [];
    const values = [zones.productStart, zones.productEnd, zones.summaryStart, zones.summaryEnd].map(Number);
    if (values.some((value) => !Number.isInteger(value) || value < 1)) {
      errors.push("四个区域行号都必须是正整数");
    } else if (!(values[0] <= values[1] && values[1] < values[2] && values[2] <= values[3])) {
      errors.push("必须满足：商品开始 ≤ 商品结束 < 汇总开始 ≤ 汇总结束");
    }
    const formulaColumns = new Set<string>();
    formulaRows.forEach((row) => {
      const col = row.col.trim().toUpperCase();
      if (!COL_LETTER_RE.test(col) || formulaColumns.has(col)) errors.push("商品公式列无效或重复");
      if (!row.formula.startsWith("=") || !row.formula.includes("{row}")) errors.push(`公式列 ${col || "?"} 必须以 = 开头并包含 {row}`);
      if (columnRows.some((item) => item.col.trim().toUpperCase() === col)) errors.push(`公式列 ${col} 不能同时映射普通字段`);
      formulaColumns.add(col);
    });
    summaryRows.forEach((row) => {
      if (!CELL_REF_RE.test(row.cell.trim().toUpperCase())) errors.push("汇总公式单元格无效");
      if (!SUMMARY_TYPES.includes(row.type as typeof SUMMARY_TYPES[number])) errors.push("汇总公式类型无效");
      if (row.type === "relative" && (!row.formula.startsWith("=") || !row.formula.includes("{"))) errors.push("相对汇总公式必须以 = 开头并引用前序结果");
    });
    externalRows.forEach((row) => {
      if (!CELL_REF_RE.test(row.cell.trim().toUpperCase()) || !row.formula.startsWith("=")) errors.push("跨区引用必须包含有效单元格和公式");
    });
    return [...new Set(errors)];
  }, [zones, formulaRows, summaryRows, externalRows, columnRows]);

  const hasErrors = Object.keys(headerErrors).length > 0 || Object.keys(columnErrors).length > 0 || structureErrors.length > 0;

  const handleSave = async () => {
    if (hasErrors) return;
    setSaving(true);
    try {
      const next: ZoneConfigEditable = {
        ...initial,
        header_fields: Object.fromEntries(
          headerRows
            .filter((r) => r.cell.trim() && r.path.trim())
            .map((r) => [r.cell.trim().toUpperCase(), r.path.trim()])
        ),
        header_formats: Object.fromEntries(
          headerRows
            .filter((r) => r.cell.trim() && r.format.trim())
            .map((r) => [r.cell.trim().toUpperCase(), r.format.trim()])
        ),
        zones: {
          product_data: { start: Number(zones.productStart), end: Number(zones.productEnd) },
          summary: { start: Number(zones.summaryStart), end: Number(zones.summaryEnd) },
        },
        product_columns: Object.fromEntries(
          columnRows
            .filter((r) => r.col.trim() && r.field.trim())
            .map((r) => [r.col.trim().toUpperCase(), r.field.trim()])
        ),
        product_row_formulas: Object.fromEntries(
          formulaRows.map((r) => [r.col.trim().toUpperCase(), r.formula.trim()])
        ),
        summary_formulas: summaryRows.map((r) => ({
          cell: r.cell.trim().toUpperCase(),
          type: r.type,
          label: r.label.trim(),
          ...(r.col.trim() ? { col: r.col.trim().toUpperCase() } : {}),
          ...(r.formula.trim() ? { formula_template: r.formula.trim() } : {}),
        })),
        external_refs: externalRows.map((r) => ({
          cell: r.cell.trim().toUpperCase(),
          formula_template: r.formula.trim(),
        })),
      };
      await onSave(next);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-5">
      {/* Top bar */}
      <div className="flex items-center justify-between gap-4">
        <Button variant="ghost" size="sm" onClick={onCancel}>
          <ArrowLeft className="h-3.5 w-3.5 mr-1.5" />
          返回列表
        </Button>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium truncate">{templateName}</div>
          <div className="text-[11px] text-muted-foreground">编辑 zone_config — 修复 LLM 分析失误</div>
        </div>
        <Button size="sm" onClick={handleSave} disabled={hasErrors || saving}>
          <Save className="h-3.5 w-3.5 mr-1.5" />
          {saving ? "保存中..." : "保存"}
        </Button>
      </div>

      {hasErrors && (
        <div className="flex items-start gap-2 px-3 py-2 rounded-md border border-destructive/40 bg-destructive/5 text-xs text-destructive">
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
          <span>存在输入错误，请修正后再保存。{structureErrors.join("；")}</span>
        </div>
      )}

      <ScrollArea className="max-h-[65vh]">
        <div className="space-y-6 pr-2">
          {/* ─── Read-only info ─── */}
          <Section title="动态区域">
            <div className="grid grid-cols-4 gap-2">
              {([
                ["商品开始", "productStart"], ["商品结束", "productEnd"],
                ["汇总开始", "summaryStart"], ["汇总结束", "summaryEnd"],
              ] as const).map(([label, key]) => (
                <div key={key}>
                  <div className="text-[10px] text-muted-foreground mb-1">{label}</div>
                  <Input
                    type="number"
                    min={1}
                    value={zones[key]}
                    onChange={(e) => setZones({ ...zones, [key]: e.target.value })}
                    className="h-8 text-xs"
                  />
                </div>
              ))}
            </div>
            <div className="mt-2 text-[10px] text-muted-foreground leading-relaxed">
              商品行会按实际数量自动伸缩，汇总区跟随移动；保存时服务器会再次验证区域边界。
            </div>
          </Section>

          {/* ─── Header Fields Editor ─── */}
          <Section title={`头部字段 Header Fields (${headerRows.length})`}>
            <p className="text-[10px] text-muted-foreground mb-2">
              每一行代表: 在某个 cell 上填写某个数据字段。比如 <code>B2 → order_data.ship_name</code>
              表示"渲染时把订单的 ship_name 填到 B2 单元格"。
            </p>
            <div className="space-y-1.5">
              {headerRows.map((row, i) => (
                <div key={i} className={`flex items-center gap-2 ${headerErrors[i] ? "bg-destructive/5 -mx-2 px-2 py-1 rounded" : ""}`}>
                  <Input
                    value={row.cell}
                    onChange={(e) => {
                      const next = [...headerRows];
                      next[i] = { ...next[i], cell: e.target.value.toUpperCase() };
                      setHeaderRows(next);
                    }}
                    placeholder="B2"
                    className="w-20 h-8 font-mono text-xs"
                  />
                  <span className="text-muted-foreground text-xs">→</span>
                  <Select
                    value={row.path}
                    onValueChange={(val) => {
                      const next = [...headerRows];
                      next[i] = { ...next[i], path: val };
                      setHeaderRows(next);
                    }}
                  >
                    <SelectTrigger className="flex-1 h-8 text-xs">
                      <SelectValue placeholder="选择数据字段..." />
                    </SelectTrigger>
                    <SelectContent>
                      {["询价单", "订单", "供应商", "交货点"].map((group) => (
                        <div key={group}>
                          <div className="px-2 py-1 text-[10px] font-semibold text-muted-foreground">{group}</div>
                          {HEADER_DATA_PATHS.filter((p) => p.group === group).map((p) => (
                            <SelectItem key={p.value} value={p.value} className="text-xs">
                              {p.label}
                            </SelectItem>
                          ))}
                        </div>
                      ))}
                    </SelectContent>
                  </Select>
                  {/* Allow arbitrary paths too (power users) */}
                  <Input
                    value={row.path}
                    onChange={(e) => {
                      const next = [...headerRows];
                      next[i] = { ...next[i], path: e.target.value };
                      setHeaderRows(next);
                    }}
                    placeholder="或直接输入路径"
                    className="flex-1 h-8 font-mono text-[11px]"
                  />
                  <Input
                    value={row.format}
                    onChange={(e) => {
                      const next = [...headerRows];
                      next[i] = { ...next[i], format: e.target.value };
                      setHeaderRows(next);
                    }}
                    placeholder="显示格式，如 TEL:{value}"
                    className="w-44 h-8 font-mono text-[11px]"
                  />
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8 text-muted-foreground hover:text-destructive"
                    onClick={() => setHeaderRows(headerRows.filter((_, j) => j !== i))}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ))}
              {headerRows.length === 0 && (
                <div className="text-xs text-muted-foreground py-2">没有头部字段。点击下方按钮添加。</div>
              )}
            </div>
            <Button
              variant="outline"
              size="sm"
              className="mt-2 h-7 text-xs"
              onClick={() => setHeaderRows([...headerRows, { cell: "", path: "", format: "" }])}
            >
              <Plus className="h-3 w-3 mr-1" />
              新增字段
            </Button>
          </Section>

          {/* ─── Product Columns Editor ─── */}
          <Section title={`产品列 Product Columns (${columnRows.length})`}>
            <p className="text-[10px] text-muted-foreground mb-2">
              每一行代表: 产品表的某一列 = 产品字典的某个字段。比如 <code>D → product_name</code>
              表示"渲染每个产品行时，把产品名写到 D 列"。
            </p>
            <div className="space-y-1.5">
              {columnRows.map((row, i) => (
                <div key={i} className={`flex items-center gap-2 ${columnErrors[i] ? "bg-destructive/5 -mx-2 px-2 py-1 rounded" : ""}`}>
                  <Input
                    value={row.col}
                    onChange={(e) => {
                      const next = [...columnRows];
                      next[i] = { ...next[i], col: e.target.value.toUpperCase() };
                      setColumnRows(next);
                    }}
                    placeholder="D"
                    className="w-16 h-8 font-mono text-xs"
                  />
                  <span className="text-muted-foreground text-xs">→</span>
                  <Select
                    value={row.field}
                    onValueChange={(val) => {
                      const next = [...columnRows];
                      next[i] = { ...next[i], field: val };
                      setColumnRows(next);
                    }}
                  >
                    <SelectTrigger className="flex-1 h-8 text-xs">
                      <SelectValue placeholder="选择字段..." />
                    </SelectTrigger>
                    <SelectContent>
                      {PRODUCT_FIELDS.map((p) => (
                        <SelectItem key={p.value} value={p.value} className="text-xs">
                          {p.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Input
                    value={row.field}
                    onChange={(e) => {
                      const next = [...columnRows];
                      next[i] = { ...next[i], field: e.target.value };
                      setColumnRows(next);
                    }}
                    placeholder="或直接输入字段名"
                    className="flex-1 h-8 font-mono text-[11px]"
                  />
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8 text-muted-foreground hover:text-destructive"
                    onClick={() => setColumnRows(columnRows.filter((_, j) => j !== i))}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ))}
              {columnRows.length === 0 && (
                <div className="text-xs text-muted-foreground py-2">没有产品列。点击下方按钮添加。</div>
              )}
            </div>
            <Button
              variant="outline"
              size="sm"
              className="mt-2 h-7 text-xs"
              onClick={() => setColumnRows([...columnRows, { col: "", field: "" }])}
            >
              <Plus className="h-3 w-3 mr-1" />
              新增列
            </Button>
          </Section>

          {/* ─── Summary formulas (read-only display) ─── */}
          <Section title={`商品行公式 (${formulaRows.length})`}>
            <EditableRows
              rows={formulaRows}
              onChange={setFormulaRows}
              fields={["col", "formula"]}
              placeholders={["L", "=H{row}*J{row}"]}
              uppercaseFirst
            />
            <AddButton onClick={() => setFormulaRows([...formulaRows, { col: "", formula: "" }])} label="新增商品公式" />
          </Section>

          <Section title={`汇总公式 (${summaryRows.length})`}>
            <div className="space-y-1.5">
              {summaryRows.map((row, i) => (
                <div key={i} className="grid grid-cols-[72px_120px_120px_72px_1fr_32px] gap-2">
                  <Input value={row.cell} onChange={(e) => updateSummary(i, "cell", e.target.value.toUpperCase())} placeholder="L33" className="h-8 text-xs font-mono" />
                  <Select value={row.type} onValueChange={(value) => updateSummary(i, "type", value)}>
                    <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="product_sum">商品合计</SelectItem>
                      <SelectItem value="relative">相对公式</SelectItem>
                    </SelectContent>
                  </Select>
                  <Input value={row.label} onChange={(e) => updateSummary(i, "label", e.target.value)} placeholder="Sub Total" className="h-8 text-xs" />
                  <Input value={row.col} onChange={(e) => updateSummary(i, "col", e.target.value.toUpperCase())} placeholder="求和列" className="h-8 text-xs font-mono" />
                  <Input value={row.formula} onChange={(e) => updateSummary(i, "formula", e.target.value)} placeholder="={sum_cell}*0.08" className="h-8 text-xs font-mono" />
                  <DeleteButton onClick={() => setSummaryRows(summaryRows.filter((_, j) => j !== i))} />
                </div>
              ))}
            </div>
            <AddButton onClick={() => setSummaryRows([...summaryRows, { cell: "", type: "relative", label: "", col: "", formula: "" }])} label="新增汇总公式" />
          </Section>

          <Section title={`跨区引用 (${externalRows.length})`}>
            <EditableRows
              rows={externalRows}
              onChange={setExternalRows}
              fields={["cell", "formula"]}
              placeholders={["H16", "={grand_total_cell}"]}
              uppercaseFirst
            />
            <AddButton onClick={() => setExternalRows([...externalRows, { cell: "", formula: "" }])} label="新增跨区引用" />
          </Section>
        </div>
      </ScrollArea>
    </div>
  );

  function updateSummary(
    index: number,
    key: keyof typeof summaryRows[number],
    value: string,
  ) {
    const next = [...summaryRows];
    next[index] = { ...next[index], [key]: value };
    setSummaryRows(next);
  }
}


function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <Label className="text-xs font-medium">{title}</Label>
      <div className="mt-2">{children}</div>
    </div>
  );
}


function AddButton({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <Button variant="outline" size="sm" className="mt-2 h-7 text-xs" onClick={onClick}>
      <Plus className="h-3 w-3 mr-1" />{label}
    </Button>
  );
}

function DeleteButton({ onClick }: { onClick: () => void }) {
  return (
    <Button variant="ghost" size="icon" className="h-8 w-8 hover:text-destructive" onClick={onClick}>
      <Trash2 className="h-3.5 w-3.5" />
    </Button>
  );
}

function EditableRows<T extends Record<string, string>>({
  rows,
  onChange,
  fields,
  placeholders,
  uppercaseFirst = false,
}: {
  rows: T[];
  onChange: (rows: T[]) => void;
  fields: Array<keyof T>;
  placeholders: string[];
  uppercaseFirst?: boolean;
}) {
  return (
    <div className="space-y-1.5">
      {rows.map((row, i) => (
        <div key={i} className="flex gap-2">
          {fields.map((field, fieldIndex) => (
            <Input
              key={String(field)}
              value={row[field]}
              onChange={(e) => {
                const next = [...rows];
                next[i] = {
                  ...next[i],
                  [field]: uppercaseFirst && fieldIndex === 0
                    ? e.target.value.toUpperCase()
                    : e.target.value,
                };
                onChange(next);
              }}
              placeholder={placeholders[fieldIndex]}
              className={`${fieldIndex === 0 ? "w-20" : "flex-1"} h-8 text-xs font-mono`}
            />
          ))}
          <DeleteButton onClick={() => onChange(rows.filter((_, j) => j !== i))} />
        </div>
      ))}
    </div>
  );
}
