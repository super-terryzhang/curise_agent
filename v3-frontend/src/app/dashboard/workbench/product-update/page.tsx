"use client";

import {
  useCallback,
  useDeferredValue,
  useEffect,
  useRef,
  useState,
} from "react";
import { ArrowLeft } from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/card";
import { WorkflowStepBar } from "@/components/workbench/workflow-step-bar";
import {
  listCategories,
  listCountries,
  listPorts,
  listProducts,
  listSuppliers,
  type ProductItem,
} from "@/lib/data-api";
import { loadProductsForExport } from "@/lib/export-products";
import {
  buildDirectUpdateRequest,
  changedRows,
  createEditRows,
  matchPeriodTargets,
  type EditOperation,
  type EditRow,
  type EditScope,
} from "@/lib/product-batch-edit";
import {
  completedDirectResult,
  commitDirectProductUpdate,
  getDirectProductUpdate,
  prepareDirectProductUpdate,
} from "@/lib/product-batch-edit-api";
import {
  cancelProductBatch,
  loadAllProductBatchRows,
  type CommitResult,
  type WorkflowBatch,
  type WorkflowRow,
} from "@/lib/product-upload-api";
import { CompletionStep } from "./completion-step";
import { DirectReviewStep } from "./direct-review-step";
import { EditDataStep, type EditMasters } from "./edit-data-step";
import { OperationStep } from "./operation-step";
import {
  ProductSelectionStep,
  type ProductSelectionFilters,
} from "./product-selection-step";

const STEPS = ["选择产品", "选择操作", "编辑数据", "核对并保存"] as const;
const INITIAL_FILTERS: ProductSelectionFilters = {
  search: "",
  category: "all",
  supplier: "all",
  country: "all",
  port: "all",
  status: "all",
};
function query(filters: ProductSelectionFilters) {
  return {
    search: filters.search || undefined,
    category_id:
      filters.category === "all" ? undefined : Number(filters.category),
    supplier_id:
      filters.supplier === "all" ? undefined : Number(filters.supplier),
    country_id: filters.country === "all" ? undefined : Number(filters.country),
    port_id: filters.port === "all" ? undefined : Number(filters.port),
    is_effective:
      filters.status === "all" ? undefined : filters.status === "effective",
  };
}
export default function ExistingProductUpdatePage() {
  const router = useRouter();
  const [stage, setStage] = useState(1);
  const [products, setProducts] = useState<ProductItem[]>([]);
  const [selected, setSelected] = useState<Record<number, ProductItem>>({});
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [filters, setFilters] = useState(INITIAL_FILTERS);
  const deferredSearch = useDeferredValue(filters.search);
  const [masters, setMasters] = useState<EditMasters>({
    categories: [],
    suppliers: [],
    countries: [],
    ports: [],
  });
  const [loading, setLoading] = useState(true);
  const [productsError, setProductsError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selectingAll, setSelectingAll] = useState(false);
  const [scope, setScope] = useState<EditScope>("basic");
  const [operation, setOperation] = useState<EditOperation>("edit");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [rows, setRows] = useState<EditRow[]>([]);
  const [batch, setBatch] = useState<WorkflowBatch | null>(null);
  const [reviewRows, setReviewRows] = useState<WorkflowRow[]>([]);
  const [result, setResult] = useState<CommitResult | null>(null);
  const requestSequence = useRef(0);
  const selectionSequence = useRef(0);
  const selectedProducts = Object.values(selected).sort((a, b) => a.id - b.id);

  useEffect(() => {
    let active = true;
    Promise.all([
      listCategories(),
      listSuppliers(),
      listCountries(),
      listPorts(),
    ])
      .then(([categories, suppliers, countries, ports]) => {
        if (active) setMasters({ categories, suppliers, countries, ports });
      })
      .catch((e) => {
        if (active)
          toast.error(e instanceof Error ? e.message : "读取筛选条件失败");
      });
    return () => {
      active = false;
    };
  }, []);
  const loadProducts = useCallback(async () => {
    const seq = ++requestSequence.current;
    setLoading(true);
    setProductsError(null);
    try {
      const response = await listProducts({
        ...query({ ...filters, search: deferredSearch }),
        limit: 20,
        offset: page * 20,
      });
      if (seq === requestSequence.current) {
        setProducts(response.items);
        setTotal(response.total);
      }
    } catch (e) {
      if (seq === requestSequence.current)
        setProductsError(e instanceof Error ? e.message : "读取产品失败");
    } finally {
      if (seq === requestSequence.current) setLoading(false);
    }
  }, [filters, deferredSearch, page]);
  useEffect(() => {
    void loadProducts();
  }, [loadProducts]);
  useEffect(
    () => () => {
      requestSequence.current++;
      selectionSequence.current++;
    },
    [],
  );

  const selectAll = async () => {
    const seq = ++selectionSequence.current;
    setSelectingAll(true);
    setError(null);
    try {
      const all = await loadProductsForExport(query(filters));
      if (seq === selectionSequence.current) {
        setSelected(Object.fromEntries(all.map((p) => [p.id, p])));
        toast.success("已选择筛选结果全部 " + all.length + " 个产品");
      }
    } catch (e) {
      if (seq === selectionSequence.current)
        setError(
          e instanceof Error ? e.message : "读取全部产品失败，原选择保持不变",
        );
    } finally {
      if (seq === selectionSequence.current) setSelectingAll(false);
    }
  };
  const selectProducts = (items: ProductItem[], checked: boolean) => {
    selectionSequence.current++;
    setSelectingAll(false);
    setSelected((previous) => {
      const next = { ...previous };
      for (const p of items) {
        if (checked) next[p.id] = p;
        else delete next[p.id];
      }
      return next;
    });
  };
  const cancelBatch = async () => {
    if (batch && !result) {
      const latest = await getDirectProductUpdate(batch.id);
      if (latest.status === "completed") {
        setResult(completedDirectResult(latest));
        return false;
      }
      if (latest.status === "resolved") {
        try {
          await cancelProductBatch(batch.id);
        } catch (error) {
          const after = await getDirectProductUpdate(batch.id);
          if (after.status === "completed") {
            setResult(completedDirectResult(after));
            return false;
          }
          throw error;
        }
      }
      setBatch(null);
    }
    return true;
  };
  const prepare = async () => {
    setBusy(true);
    setError(null);
    try {
      const request = buildDirectUpdateRequest(
        selectedProducts,
        scope,
        operation,
        rows,
      );
      if (!(await cancelBatch())) return;
      const prepared = await prepareDirectProductUpdate(request);
      setBatch(prepared);
      const response = await loadAllProductBatchRows(prepared.id, "all", true);
      const pending = changedRows(rows, operation);
      setReviewRows(
        response.items.map((row) => ({
          ...row,
          source_row_number:
            rows.findIndex(
              (item) => item.key === pending[row.source_row_number - 1]?.key,
            ) + 1,
        })),
      );
      setStage(4);
    } catch (e) {
      setError(e instanceof Error ? e.message : "检查失败，编辑内容已保留");
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    if (!batch?.can_continue || busy) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await commitDirectProductUpdate(batch.id);
      if (saved.errors || saved.created)
        throw new Error("保存结果与更新范围不一致，请检查处理记录");
      setResult(saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败，编辑内容已保留");
    } finally {
      setBusy(false);
    }
  };
  const returnToEdit = async () => {
    setBusy(true);
    setError(null);
    try {
      if (await cancelBatch()) setStage(3);
    } catch (e) {
      setError(e instanceof Error ? e.message : "取消旧检查失败，请重试");
    } finally {
      setBusy(false);
    }
  };
  const leave = async () => {
    if (busy) return;
    if (
      stage >= 3 &&
      !result &&
      changedRows(rows, operation).length &&
      !window.confirm("离开后，本次尚未保存的编辑内容将丢失。确定返回工作台？")
    )
      return;
    setBusy(true);
    try {
      await cancelBatch();
      router.push("/dashboard/workbench");
    } catch (e) {
      setError(e instanceof Error ? e.message : "取消批次失败");
      setBusy(false);
    }
  };
  useEffect(() => {
    if (stage < 3 || result || !changedRows(rows, operation).length) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [stage, result, rows, operation]);
  return (
    <div className="h-full overflow-y-auto bg-muted/20">
      <div className="mx-auto max-w-[1600px] px-6 py-6">
        <div className="mb-4">
          <button
            className="mb-2 flex items-center gap-1 text-xs text-muted-foreground"
            disabled={busy}
            onClick={() => void leave()}
          >
            <ArrowLeft className="size-3.5" />
            返回工作台
          </button>
          <h1 className="text-lg font-semibold">已有产品更新</h1>
          <p className="mt-1 text-xs text-muted-foreground">
            直接在页面修改基本信息和价格区间，检查并核对后保存。
          </p>
        </div>
        <Card className="gap-0 overflow-hidden rounded-md py-0 shadow-sm">
          <WorkflowStepBar labels={STEPS} current={stage} />
          <CardContent className="p-6">
            {error && (
              <div
                role="alert"
                className="mb-4 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
              >
                {error}
              </div>
            )}
            {result ? (
              <CompletionStep
                result={result}
                onReset={() => {
                  setResult(null);
                  setBatch(null);
                  setRows([]);
                  setSelected({});
                  setError(null);
                  setStage(1);
                  void loadProducts();
                }}
              />
            ) : (
              <fieldset disabled={busy} className="min-w-0">
                {stage === 1 && (
                  <ProductSelectionStep
                    products={products}
                    total={total}
                    page={page}
                    pageSize={20}
                    selectedProducts={selected}
                    filters={filters}
                    {...masters}
                    loading={loading}
                    error={productsError}
                    onFiltersChange={(next) => {
                      selectionSequence.current++;
                      setSelectingAll(false);
                      setFilters(next);
                      setPage(0);
                      setError(null);
                    }}
                    onSelectProduct={(p, checked) =>
                      selectProducts([p], checked)
                    }
                    onSelectVisible={selectProducts}
                    onClearSelection={() => {
                      selectionSequence.current++;
                      setSelectingAll(false);
                      setSelected({});
                    }}
                    onPageChange={setPage}
                    onRetry={() => void loadProducts()}
                    onSelectAllFiltered={() => void selectAll()}
                    selectingAll={selectingAll}
                    onNext={() => {
                      setError(null);
                      setStage(2);
                    }}
                  />
                )}
                {stage === 2 && (
                  <OperationStep
                    products={selectedProducts}
                    scope={scope}
                    operation={operation}
                    from={from}
                    to={to}
                    onChange={(s, o, f, t) => {
                      setScope(s);
                      setOperation(o);
                      setFrom(f);
                      setTo(t);
                    }}
                    onBack={() => setStage(1)}
                    onNext={() => {
                      const targets =
                        scope === "basic"
                          ? []
                          : matchPeriodTargets(
                              selectedProducts,
                              scope,
                              from,
                              to,
                            ).targets;
                      setRows(
                        createEditRows(
                          selectedProducts,
                          scope,
                          operation,
                          targets,
                        ),
                      );
                      setError(null);
                      setStage(3);
                    }}
                  />
                )}
                {stage === 3 && (
                  <EditDataStep
                    rows={rows}
                    scope={scope}
                    operation={operation}
                    masters={masters}
                    busy={busy}
                    onRowsChange={setRows}
                    onBack={() => {
                      if (
                        !changedRows(rows, operation).length ||
                        window.confirm(
                          "返回后会重新生成编辑表格，本次填写将丢失。确定返回？",
                        )
                      ) {
                        setError(null);
                        setStage(2);
                      }
                    }}
                    onCheck={() => void prepare()}
                  />
                )}
                {stage === 4 && batch && (
                  <DirectReviewStep
                    batch={batch}
                    rows={reviewRows}
                    busy={busy}
                    onBack={() => void returnToEdit()}
                    onSave={() => void save()}
                  />
                )}
              </fieldset>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
