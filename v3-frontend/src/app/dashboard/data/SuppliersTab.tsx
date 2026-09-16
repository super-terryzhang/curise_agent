"use client";

import { useEffect, useState, useCallback } from "react";
import { type ColumnDef } from "@tanstack/react-table";
import { DataTable } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState } from "@/components/empty-state";
import { AlertTriangle, CheckCircle2, Loader2, Truck, Plus, MoreHorizontal } from "lucide-react";
import { toast } from "sonner";
import { getUser } from "@/lib/auth";
import {
  listSuppliers,
  listCountries,
  listCategories,
  createSupplier,
  updateSupplier,
  deleteSupplier,
  type SupplierItem,
  type CountryItem,
  type CategoryItem,
} from "@/lib/data-api";

// The 5 letterhead-only fields (everything past the "identity" group).
// Names match the `suppliers` DB columns so we can spread them straight
// into the PATCH payload without remapping. Used by `_letterheadFilled`
// below to compute the "信头" indicator column.
const LETTERHEAD_FIELDS = [
  "contact",
  "email",
  "phone",
  "address",
  "zip_code",
  "fax",
  "default_payment_method",
  "default_payment_terms",
] as const;

function _filledCount(s: SupplierItem): number {
  return LETTERHEAD_FIELDS.reduce((n, k) => {
    // SupplierItem has these keys explicitly typed as `string | null`, so
    // double-cast via unknown is required under strict TS to satisfy the
    // structural check on indexed access.
    const v = (s as unknown as Record<string, unknown>)[k];
    return n + (typeof v === "string" && v.trim() ? 1 : 0);
  }, 0);
}

function LetterheadBadge({ supplier }: { supplier: SupplierItem }) {
  const filled = _filledCount(supplier);
  const missing = LETTERHEAD_FIELDS.length - filled;
  if (missing === 0) {
    return (
      <Badge
        variant="secondary"
        className="bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300 text-[10px] gap-1"
      >
        <CheckCircle2 className="h-2.5 w-2.5" />
        信头完整
      </Badge>
    );
  }
  const empties = LETTERHEAD_FIELDS.filter((k) => {
    const v = (supplier as unknown as Record<string, unknown>)[k];
    return !(typeof v === "string" && v.trim());
  });
  return (
    <Badge
      variant="secondary"
      className="bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300 text-[10px] gap-1"
      title={`缺：${empties.join(", ")}`}
    >
      <AlertTriangle className="h-2.5 w-2.5" />
      缺 {missing}
    </Badge>
  );
}

function StatusBadge({ status }: { status: boolean | null }) {
  if (status === true || status === null) {
    return (
      <Badge variant="secondary" className="bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300">
        有效
      </Badge>
    );
  }
  return (
    <Badge variant="secondary" className="bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300">
      无效
    </Badge>
  );
}

export default function SuppliersTab() {
  const [data, setData] = useState<SupplierItem[]>([]);
  const [countries, setCountries] = useState<CountryItem[]>([]);
  const [categories, setCategories] = useState<CategoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<SupplierItem | null>(null);
  const [saving, setSaving] = useState(false);
  // Form state mirrors the writable subset of SupplierItem. Strings instead
  // of nullable since HTML inputs always produce strings; we convert to
  // undefined/null at PATCH time. country_id stays a string so the Select
  // value can be the empty string for "no country".
  const [form, setForm] = useState({
    name: "",
    country_id: "",
    contact: "",
    email: "",
    phone: "",
    address: "",
    zip_code: "",
    fax: "",
    default_payment_method: "",
    default_payment_terms: "",
    category_ids: [] as number[],
  });

  const isWriter = (() => {
    const user = getUser();
    return user?.role === "superadmin" || user?.role === "admin";
  })();

  const reload = useCallback(() => {
    listSuppliers()
      .then(setData)
      .catch((err) => toast.error(err.message));
  }, []);

  useEffect(() => {
    Promise.all([listSuppliers(), listCountries(), listCategories()])
      .then(([s, c, cat]) => {
        setData(s);
        setCountries(c);
        setCategories(cat);
      })
      .catch((err) => toast.error(err.message))
      .finally(() => setLoading(false));
  }, []);

  function openCreate() {
    setEditing(null);
    setForm({
      name: "",
      country_id: "",
      contact: "",
      email: "",
      phone: "",
      address: "",
      zip_code: "",
      fax: "",
      default_payment_method: "",
      default_payment_terms: "",
      category_ids: [],
    });
    setDialogOpen(true);
  }

  function openEdit(item: SupplierItem) {
    setEditing(item);
    setForm({
      name: item.name,
      country_id: item.country_id ? String(item.country_id) : "",
      contact: item.contact || "",
      email: item.email || "",
      phone: item.phone || "",
      address: item.address || "",
      zip_code: item.zip_code || "",
      fax: item.fax || "",
      default_payment_method: item.default_payment_method || "",
      default_payment_terms: item.default_payment_terms || "",
      category_ids: item.category_ids || [],
    });
    setDialogOpen(true);
  }

  function toggleCategory(catId: number) {
    setForm((prev) => ({
      ...prev,
      category_ids: prev.category_ids.includes(catId)
        ? prev.category_ids.filter((id) => id !== catId)
        : [...prev.category_ids, catId],
    }));
  }

  async function handleSave() {
    if (!form.name.trim()) {
      toast.error("名称不能为空");
      return;
    }
    setSaving(true);
    try {
      // Build the payload as a plain object so we can omit empty
      // letterhead fields cleanly. Empty trimmed strings become `null` on
      // PATCH (clears the column) so the user can wipe a wrong value;
      // truly absent fields are not present in the payload at all.
      const trimOrNull = (s: string): string | null => {
        const t = s.trim();
        return t === "" ? null : t;
      };
      const payload = {
        name: form.name.trim(),
        country_id: form.country_id
          ? Number(form.country_id)
          : (editing ? null : undefined),
        contact: trimOrNull(form.contact),
        email: trimOrNull(form.email),
        phone: trimOrNull(form.phone),
        address: trimOrNull(form.address),
        zip_code: trimOrNull(form.zip_code),
        fax: trimOrNull(form.fax),
        default_payment_method: trimOrNull(form.default_payment_method),
        default_payment_terms: trimOrNull(form.default_payment_terms),
        category_ids: form.category_ids,
      };
      if (editing) {
        await updateSupplier(editing.id, payload);
        toast.success("更新成功");
      } else {
        await createSupplier(payload);
        toast.success("创建成功");
      }
      setDialogOpen(false);
      reload();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "操作失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleToggleStatus(item: SupplierItem) {
    try {
      await updateSupplier(item.id, { status: !item.status });
      toast.success(item.status ? "已停用" : "已启用");
      reload();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "操作失败");
    }
  }

  async function handleDelete(item: SupplierItem) {
    if (!confirm(`确定要删除供应商「${item.name}」吗？`)) return;
    try {
      await deleteSupplier(item.id);
      toast.success("删除成功");
      reload();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    }
  }

  const columns: ColumnDef<SupplierItem>[] = [
    {
      accessorKey: "name",
      header: "供应商名称",
      cell: ({ row }) => (
        <span className="font-medium">{row.original.name}</span>
      ),
    },
    {
      accessorKey: "country_name",
      header: "国家",
      size: 100,
      cell: ({ row }) => row.original.country_name || "-",
    },
    {
      accessorKey: "contact",
      header: "联系人",
      size: 100,
      cell: ({ row }) => row.original.contact || "-",
    },
    {
      accessorKey: "email",
      header: "邮箱",
      size: 180,
      cell: ({ row }) => (
        <span className="text-muted-foreground">
          {row.original.email || "-"}
        </span>
      ),
    },
    {
      accessorKey: "phone",
      header: "电话",
      size: 120,
      cell: ({ row }) => row.original.phone || "-",
    },
    {
      accessorKey: "address",
      header: "地址",
      size: 200,
      cell: ({ row }) => (
        <span
          className="block max-w-[200px] truncate text-muted-foreground"
          title={row.original.address || undefined}
        >
          {row.original.address || "-"}
        </span>
      ),
    },
    {
      accessorKey: "zip_code",
      header: "邮编",
      size: 80,
      cell: ({ row }) => row.original.zip_code || "-",
    },
    {
      accessorKey: "fax",
      header: "传真",
      size: 120,
      cell: ({ row }) => row.original.fax || "-",
    },
    {
      accessorKey: "default_payment_method",
      header: "付款方式",
      size: 110,
      cell: ({ row }) => row.original.default_payment_method || "-",
    },
    {
      accessorKey: "default_payment_terms",
      header: "付款条款",
      size: 110,
      cell: ({ row }) => row.original.default_payment_terms || "-",
    },
    {
      accessorKey: "categories",
      header: "经营类别",
      size: 200,
      cell: ({ row }) => {
        const cats = row.original.categories;
        if (!cats.length) return "-";
        return (
          <div className="flex flex-wrap gap-1">
            {cats.slice(0, 3).map((c) => (
              <Badge key={c} variant="outline" className="text-[10px]">{c}</Badge>
            ))}
            {cats.length > 3 && (
              <Badge variant="secondary" className="text-[10px]">+{cats.length - 3}</Badge>
            )}
          </div>
        );
      },
    },
    {
      id: "letterhead",
      header: "信头",
      size: 90,
      cell: ({ row }) => <LetterheadBadge supplier={row.original} />,
    },
    {
      accessorKey: "status",
      header: "状态",
      size: 70,
      cell: ({ row }) => <StatusBadge status={row.original.status} />,
    },
    ...(isWriter
      ? [
          {
            id: "actions",
            header: "操作",
            size: 60,
            cell: ({ row }: { row: { original: SupplierItem } }) => (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                    <MoreHorizontal className="h-4 w-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => openEdit(row.original)}>
                    编辑
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => handleToggleStatus(row.original)}>
                    {row.original.status ? "停用" : "启用"}
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    className="text-red-600"
                    onClick={() => handleDelete(row.original)}
                  >
                    删除
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            ),
          } as ColumnDef<SupplierItem>,
        ]
      : []),
  ];

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const toolbar = isWriter ? (
    <div className="flex items-center gap-2 flex-1 justify-end">
      <Button size="sm" className="h-8 text-xs" onClick={openCreate}>
        <Plus className="mr-1 h-3 w-3" /> 新增供应商
      </Button>
    </div>
  ) : undefined;

  return (
    <>
      <DataTable
        columns={columns}
        data={data}
        searchKey="name"
        searchPlaceholder="搜索供应商..."
        // listSuppliers() returns ALL rows (no server-side pagination on
        // this endpoint; the table is intentionally a small lookup —
        // typically <100 rows). pageSize=1000 makes DataTable's
        // `pageCount > 1` guard hide the "下一页" button entirely when
        // the data fits, instead of pretending to paginate client-side.
        pageSize={1000}
        toolbar={toolbar}
        emptyState={<EmptyState icon={Truck} title="暂无供应商数据" />}
        // Address-block fields exist on every supplier and are editable
        // in the dialog, but most users only need them for inquiry
        // generation. Hide them from the default table view; users
        // toggle them on via the "列" button when they want a full audit.
        defaultHiddenColumns={[
          "phone",
          "address",
          "zip_code",
          "fax",
        ]}
        visibilityStorageKey="v3.data.suppliers.cols"
      />

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        {/* max-w-2xl + max-h-[85vh] overflow so the 9-field letterhead
            section fits on laptop screens without the footer scrolling
            out of reach. */}
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editing ? "编辑供应商" : "新增供应商"}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-5 py-4">
            {/* ── Section 1: 身份信息 — affects order matching ── */}
            <section className="space-y-3">
              <div className="flex items-baseline gap-2">
                <h4 className="text-sm font-semibold">身份信息</h4>
                <span className="text-[11px] text-muted-foreground">
                  影响订单匹配，请谨慎修改
                </span>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="grid gap-2">
                  <Label>供应商名称 *</Label>
                  <Input
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    placeholder="供应商名称"
                  />
                </div>
                <div className="grid gap-2">
                  <Label>所属国家</Label>
                  <Select
                    value={form.country_id}
                    onValueChange={(v) =>
                      setForm({ ...form, country_id: v === "__none__" ? "" : v })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="选择国家" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">无</SelectItem>
                      {countries.map((c) => (
                        <SelectItem key={c.id} value={String(c.id)}>
                          {c.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="grid gap-2">
                <Label>经营类别</Label>
                <div className="flex flex-wrap gap-2 p-3 border rounded-md min-h-[40px]">
                  {categories.map((cat) => {
                    const selected = form.category_ids.includes(cat.id);
                    return (
                      <Badge
                        key={cat.id}
                        variant={selected ? "default" : "outline"}
                        className={`cursor-pointer transition-colors ${
                          selected ? "" : "opacity-60 hover:opacity-100"
                        }`}
                        onClick={() => toggleCategory(cat.id)}
                      >
                        {cat.name}
                      </Badge>
                    );
                  })}
                  {categories.length === 0 && (
                    <span className="text-xs text-muted-foreground">暂无类别数据</span>
                  )}
                </div>
              </div>
            </section>

            {/* ── Section 2: 询价单信头字段 — printed on the Excel ── */}
            <section className="space-y-3">
              <div className="flex items-baseline gap-2">
                <h4 className="text-sm font-semibold">询价单信头字段</h4>
                <span className="text-[11px] text-muted-foreground">
                  会印在发给供应商的 Excel 询价单上（如地址、付款条件）
                </span>
              </div>
              <div className="grid grid-cols-3 gap-4">
                <div className="grid gap-2">
                  <Label>联系人</Label>
                  <Input
                    value={form.contact}
                    onChange={(e) => setForm({ ...form, contact: e.target.value })}
                  />
                </div>
                <div className="grid gap-2">
                  <Label>邮箱</Label>
                  <Input
                    value={form.email}
                    onChange={(e) => setForm({ ...form, email: e.target.value })}
                    type="email"
                  />
                </div>
                <div className="grid gap-2">
                  <Label>电话</Label>
                  <Input
                    value={form.phone}
                    onChange={(e) => setForm({ ...form, phone: e.target.value })}
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="grid gap-2">
                  <Label>传真</Label>
                  <Input
                    value={form.fax}
                    onChange={(e) => setForm({ ...form, fax: e.target.value })}
                  />
                </div>
                <div className="grid gap-2">
                  <Label>邮编</Label>
                  <Input
                    value={form.zip_code}
                    onChange={(e) => setForm({ ...form, zip_code: e.target.value })}
                  />
                </div>
              </div>
              <div className="grid gap-2">
                <Label>地址</Label>
                <Input
                  value={form.address}
                  onChange={(e) => setForm({ ...form, address: e.target.value })}
                  placeholder="例：東京都中央区晴海3-1-1"
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="grid gap-2">
                  <Label>默认付款方式</Label>
                  <Input
                    value={form.default_payment_method}
                    onChange={(e) =>
                      setForm({ ...form, default_payment_method: e.target.value })
                    }
                    placeholder="例：T/T、L/C"
                  />
                </div>
                <div className="grid gap-2">
                  <Label>默认付款条件</Label>
                  <Input
                    value={form.default_payment_terms}
                    onChange={(e) =>
                      setForm({ ...form, default_payment_terms: e.target.value })
                    }
                    placeholder="例：Net 30、月末締め翌月末"
                  />
                </div>
              </div>
            </section>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              取消
            </Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {editing ? "保存" : "创建"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
