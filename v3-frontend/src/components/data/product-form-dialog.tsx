"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import {
  createProduct,
  listProducts,
  updateProduct,
  type CategoryItem,
  type CountryItem,
  type PortItem,
  type ProductCreateData,
  type ProductItem,
  type SupplierItem,
} from "@/lib/data-api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  calculateProductProfitMargin,
  ProductProfitMargin,
} from "./product-profit-margin";

export interface ProductForm {
  product_name_en: string;
  product_name_jp: string;
  code: string;
  brand: string;
  country_id: string;
  category_id: string;
  supplier_id: string;
  port_id: string;
  price: string;
  contract_price: string;
  purchase_price_effective_from: string;
  purchase_price_effective_to: string;
  selling_price_effective_from: string;
  selling_price_effective_to: string;
  currency: string;
  unit: string;
  unit_size: string;
  pack_size: string;
  country_of_origin: string;
  effective_from: string;
  effective_to: string;
}

export const emptyProductForm: ProductForm = {
  product_name_en: "",
  product_name_jp: "",
  code: "",
  brand: "",
  country_id: "",
  category_id: "",
  supplier_id: "",
  port_id: "",
  price: "",
  contract_price: "",
  purchase_price_effective_from: "",
  purchase_price_effective_to: "",
  selling_price_effective_from: "",
  selling_price_effective_to: "",
  currency: "",
  unit: "",
  unit_size: "",
  pack_size: "",
  country_of_origin: "",
  effective_from: "",
  effective_to: "",
};

function dateValue(value: string | null | undefined) {
  return value?.slice(0, 10) || "";
}

export function productToForm(product: ProductItem): ProductForm {
  return {
    product_name_en: product.product_name_en || "",
    product_name_jp: product.product_name_jp || "",
    code: product.code || "",
    brand: product.brand || "",
    country_id: product.country_id ? String(product.country_id) : "",
    category_id: product.category_id ? String(product.category_id) : "",
    supplier_id: product.supplier_id ? String(product.supplier_id) : "",
    port_id: product.port_id ? String(product.port_id) : "",
    price: product.price != null ? String(product.price) : "",
    contract_price: product.contract_price != null ? String(product.contract_price) : "",
    purchase_price_effective_from: dateValue(product.purchase_price_effective_from),
    purchase_price_effective_to: dateValue(product.purchase_price_effective_to),
    selling_price_effective_from: dateValue(product.selling_price_effective_from),
    selling_price_effective_to: dateValue(product.selling_price_effective_to),
    currency: product.currency || "",
    unit: product.unit || "",
    unit_size: product.unit_size || "",
    pack_size: product.pack_size || "",
    country_of_origin: product.country_of_origin || "",
    effective_from: dateValue(product.effective_from),
    effective_to: dateValue(product.effective_to),
  };
}

export function validateProductForm(form: ProductForm): string | null {
  if (!form.product_name_en.trim()) return "英文品名不能为空";
  if (
    form.purchase_price_effective_from &&
    form.purchase_price_effective_to &&
    form.purchase_price_effective_from > form.purchase_price_effective_to
  ) {
    return "采购价有效开始日期不能晚于结束日期";
  }
  if (
    form.selling_price_effective_from &&
    form.selling_price_effective_to &&
    form.selling_price_effective_from > form.selling_price_effective_to
  ) {
    return "卖价有效开始日期不能晚于结束日期";
  }
  return null;
}

export function productFormPayload(
  form: ProductForm,
  product: ProductItem | null,
): Partial<ProductCreateData> & { product_name_en: string; expected_revision?: number } {
  const cleared = product ? null : undefined;
  const payload = {
    product_name_en: form.product_name_en.trim(),
    product_name_jp: form.product_name_jp.trim() || cleared,
    code: form.code.trim() || cleared,
    brand: form.brand.trim() || cleared,
    country_id: form.country_id ? Number(form.country_id) : cleared,
    category_id: form.category_id ? Number(form.category_id) : cleared,
    supplier_id: form.supplier_id ? Number(form.supplier_id) : cleared,
    port_id: form.port_id ? Number(form.port_id) : cleared,
    price: form.price === "" ? cleared : Number(form.price),
    contract_price: form.contract_price === "" ? cleared : Number(form.contract_price),
    purchase_price_effective_from: form.purchase_price_effective_from || cleared,
    purchase_price_effective_to: form.purchase_price_effective_to || cleared,
    selling_price_effective_from: form.selling_price_effective_from || cleared,
    selling_price_effective_to: form.selling_price_effective_to || cleared,
    currency: form.currency.trim() || cleared,
    unit: form.unit.trim() || cleared,
    unit_size: form.unit_size.trim() || cleared,
    pack_size: form.pack_size.trim() || cleared,
    country_of_origin: form.country_of_origin.trim() || cleared,
    effective_from: form.effective_from || cleared,
    effective_to: form.effective_to || cleared,
    ...(product ? { expected_revision: product.revision } : {}),
  };
  return payload;
}

interface ProductFormDialogProps {
  open: boolean;
  product: ProductItem | null;
  categories: CategoryItem[];
  suppliers: SupplierItem[];
  countries: CountryItem[];
  ports: PortItem[];
  onOpenChange: (open: boolean) => void;
  onSaved: (product: ProductItem) => void;
  onDuplicate: (product: ProductItem) => void;
}

export function ProductFormDialog({
  open,
  product,
  categories,
  suppliers,
  countries,
  ports,
  onOpenChange,
  onSaved,
  onDuplicate,
}: ProductFormDialogProps) {
  const [form, setForm] = useState<ProductForm>(emptyProductForm);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) setForm(product ? productToForm(product) : emptyProductForm);
  }, [open, product]);

  function updateForm(key: keyof ProductForm, value: string) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function handleSave() {
    const validationError = validateProductForm(form);
    if (validationError) {
      toast.error(validationError);
      return;
    }

    setSaving(true);
    try {
      const payload = productFormPayload(form, product);
      const saved = product
        ? await updateProduct(product.id, payload as Partial<ProductCreateData> & { expected_revision: number })
        : await createProduct(payload);
      toast.success(product ? "更新成功" : "创建成功");
      onOpenChange(false);
      onSaved(saved);
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : "操作失败";
      if (!product && message.includes("已存在")) {
        try {
          const { items } = await listProducts({ search: form.product_name_en.trim(), limit: 10 });
          const countryId = form.country_id ? Number(form.country_id) : null;
          const portId = form.port_id ? Number(form.port_id) : null;
          const existing = items.find(
            (item) =>
              item.product_name_en === form.product_name_en.trim() &&
              item.country_id === countryId &&
              item.port_id === portId,
          );
          if (existing) {
            onOpenChange(false);
            toast.info("该产品已存在，已为您打开编辑");
            onDuplicate(existing);
            return;
          }
        } catch {
          // Preserve the original API error when duplicate lookup is unavailable.
        }
      }
      toast.error(message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{product ? "编辑产品" : "新增产品"}</DialogTitle>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="grid grid-cols-2 gap-4">
            <FormInput label="英文品名 *" value={form.product_name_en} onChange={(value) => updateForm("product_name_en", value)} placeholder="Product name in English" />
            <FormInput label="日文品名" value={form.product_name_jp} onChange={(value) => updateForm("product_name_jp", value)} placeholder="日本語の商品名" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <FormInput label="商品代码" value={form.code} onChange={(value) => updateForm("code", value)} placeholder="例如：BEEF-001" />
            <FormInput label="品牌" value={form.brand} onChange={(value) => updateForm("brand", value)} />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <ReferenceSelect label="国家" placeholder="选择国家" value={form.country_id} items={countries} onChange={(value) => updateForm("country_id", value)} />
            <ReferenceSelect label="类别" placeholder="选择类别" value={form.category_id} items={categories} onChange={(value) => updateForm("category_id", value)} />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <ReferenceSelect label="供应商" placeholder="选择供应商" value={form.supplier_id} items={suppliers} onChange={(value) => updateForm("supplier_id", value)} />
            <ReferenceSelect label="港口" placeholder="选择港口" value={form.port_id} items={ports} onChange={(value) => updateForm("port_id", value)} />
          </div>
          <div className="grid grid-cols-4 gap-4">
            <FormInput label="采购价" type="number" value={form.price} onChange={(value) => updateForm("price", value)} placeholder="0.00" />
            <FormInput label="卖价（财务对比基线）" type="number" value={form.contract_price} onChange={(value) => updateForm("contract_price", value)} placeholder="0.00" />
            <div className="grid gap-2">
              <Label>利润率</Label>
              <div className="flex h-9 items-center rounded-md border bg-muted/30 px-3 text-sm">
                <ProductProfitMargin value={calculateProductProfitMargin(form.price, form.contract_price)} />
              </div>
            </div>
            <FormInput label="币种" value={form.currency} onChange={(value) => updateForm("currency", value)} placeholder="例如：AUD" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <FormInput label="采购价有效开始日期" type="date" value={form.purchase_price_effective_from} onChange={(value) => updateForm("purchase_price_effective_from", value)} />
            <FormInput label="采购价有效结束日期" type="date" value={form.purchase_price_effective_to} onChange={(value) => updateForm("purchase_price_effective_to", value)} />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <FormInput label="卖价有效开始日期" type="date" value={form.selling_price_effective_from} onChange={(value) => updateForm("selling_price_effective_from", value)} />
            <FormInput label="卖价有效结束日期" type="date" value={form.selling_price_effective_to} onChange={(value) => updateForm("selling_price_effective_to", value)} />
          </div>
          <div className="grid grid-cols-3 gap-4">
            <FormInput label="单位" value={form.unit} onChange={(value) => updateForm("unit", value)} placeholder="例如：KG" />
            <FormInput label="单位规格" value={form.unit_size} onChange={(value) => updateForm("unit_size", value)} placeholder="例如：10kg" />
            <FormInput label="包装规格" value={form.pack_size} onChange={(value) => updateForm("pack_size", value)} placeholder="例如：6-10ct/10kg" />
          </div>
          <FormInput label="原产地" value={form.country_of_origin} onChange={(value) => updateForm("country_of_origin", value)} placeholder="例如：Australia" />
          <div className="grid grid-cols-2 gap-4">
            <FormInput label="产品有效开始日期" type="date" value={form.effective_from} onChange={(value) => updateForm("effective_from", value)} />
            <FormInput label="产品有效结束日期" type="date" value={form.effective_to} onChange={(value) => updateForm("effective_to", value)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>取消</Button>
          <Button onClick={() => void handleSave()} disabled={saving}>
            {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {product ? "保存" : "创建"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function FormInput({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: string;
}) {
  return (
    <div className="grid gap-2">
      <Label>{label}</Label>
      <Input
        type={type}
        min={type === "number" ? 0 : undefined}
        step={type === "number" ? "0.01" : undefined}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
      />
    </div>
  );
}

function ReferenceSelect({
  label,
  placeholder,
  value,
  items,
  onChange,
}: {
  label: string;
  placeholder: string;
  value: string;
  items: Array<{ id: number; name: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <div className="grid gap-2">
      <Label>{label}</Label>
      <Select value={value} onValueChange={(next) => onChange(next === "__none__" ? "" : next)}>
        <SelectTrigger><SelectValue placeholder={placeholder} /></SelectTrigger>
        <SelectContent>
          <SelectItem value="__none__">无</SelectItem>
          {items.map((item) => <SelectItem key={item.id} value={String(item.id)}>{item.name}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
  );
}
