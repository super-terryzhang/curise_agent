"use client";

import { useCallback, useDeferredValue, useEffect, useReducer, useRef, useState } from "react";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { WorkflowStepBar } from "@/components/workbench/workflow-step-bar";
import {
  listCategories,
  listCountries,
  listPorts,
  listProducts,
  listSuppliers,
  type CategoryItem,
  type CountryItem,
  type PortItem,
  type ProductItem,
  type SupplierItem,
} from "@/lib/data-api";
import { downloadExistingProductUpdateWorkbook } from "@/lib/existing-product-update-workbook";
import {
  existingProductUpdateReducer,
  initialExistingProductUpdateState,
} from "@/lib/existing-product-update-workflow";
import {
  cancelProductBatch,
  commitProductBatch,
  loadAllProductBatchRows,
  uploadProductWorkbook,
  validateProductBatch,
  type WorkflowBatch,
} from "@/lib/product-upload-api";
import { CompletionStep } from "./completion-step";
import { ProductSelectionStep, type ProductSelectionFilters } from "./product-selection-step";
import { ReviewStep } from "./review-step";
import { UpdateScopeStep } from "./update-scope-step";
import { ValidationStep } from "./validation-step";
import { WorkbookStep } from "./workbook-step";

const STEPS = ["选择产品", "选择范围", "下载与上传", "程序检查", "核对并提交"] as const;
const PAGE_SIZE = 20;
const INITIAL_FILTERS: ProductSelectionFilters = {
  search: "",
  category: "all",
  supplier: "all",
  country: "all",
  port: "all",
  status: "all",
};

function selectedValues(products: Record<number, ProductItem>): ProductItem[] {
  return Object.values(products).sort((left, right) => left.id - right.id);
}

export default function ExistingProductUpdatePage() {
  const router = useRouter();
  const [workflow, dispatch] = useReducer(existingProductUpdateReducer, undefined, () => initialExistingProductUpdateState());
  const [products, setProducts] = useState<ProductItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [filters, setFilters] = useState<ProductSelectionFilters>(INITIAL_FILTERS);
  const deferredSearch = useDeferredValue(filters.search);
  const [categories, setCategories] = useState<CategoryItem[]>([]);
  const [suppliers, setSuppliers] = useState<SupplierItem[]>([]);
  const [countries, setCountries] = useState<CountryItem[]>([]);
  const [ports, setPorts] = useState<PortItem[]>([]);
  const [productsLoading, setProductsLoading] = useState(true);
  const [productsError, setProductsError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [validating, setValidating] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const requestSequence = useRef(0);

  useEffect(() => {
    let active = true;
    Promise.all([listCategories(), listSuppliers(), listCountries(), listPorts()])
      .then(([nextCategories, nextSuppliers, nextCountries, nextPorts]) => {
        if (!active) return;
        setCategories(nextCategories);
        setSuppliers(nextSuppliers);
        setCountries(nextCountries);
        setPorts(nextPorts);
      })
      .catch((error) => {
        if (!active) return;
        toast.error(error instanceof Error ? error.message : "读取筛选条件失败");
      });
    return () => { active = false; };
  }, []);

  const loadProducts = useCallback(async () => {
    const sequence = ++requestSequence.current;
    setProductsLoading(true);
    setProductsError(null);
    try {
      const response = await listProducts({
        search: deferredSearch || undefined,
        category_id: filters.category === "all" ? undefined : Number(filters.category),
        supplier_id: filters.supplier === "all" ? undefined : Number(filters.supplier),
        country_id: filters.country === "all" ? undefined : Number(filters.country),
        port_id: filters.port === "all" ? undefined : Number(filters.port),
        is_effective: filters.status === "all" ? undefined : filters.status === "effective",
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      });
      if (sequence !== requestSequence.current) return;
      setProducts(response.items);
      setTotal(response.total);
    } catch (error) {
      if (sequence !== requestSequence.current) return;
      setProductsError(error instanceof Error ? error.message : "读取产品失败");
    } finally {
      if (sequence === requestSequence.current) setProductsLoading(false);
    }
  }, [deferredSearch, filters.category, filters.country, filters.port, filters.status, filters.supplier, page]);

  useEffect(() => {
    void loadProducts();
  }, [loadProducts]);

  const cancelOpenBatch = useCallback(async () => {
    const batch = workflow.batch;
    if (!batch || workflow.completed || ["cancelled", "completed", "rolled_back"].includes(batch.status)) return;
    try {
      await cancelProductBatch(batch.id);
    } catch (error) {
      toast.error(error instanceof Error ? `旧批次取消失败：${error.message}` : "旧批次取消失败");
    }
  }, [workflow.batch, workflow.completed]);

  const reset = useCallback(async () => {
    await cancelOpenBatch();
    dispatch({ type: "reset" });
    setPage(0);
    setFilters(INITIAL_FILTERS);
    setFile(null);
    setFileError(null);
    setConfirmOpen(false);
  }, [cancelOpenBatch]);

  const handleDownload = async () => {
    setDownloading(true);
    setFileError(null);
    try {
      const count = await downloadExistingProductUpdateWorkbook(selectedValues(workflow.selectedProducts), workflow.scope);
      toast.success(`已生成 ${count} 个产品的更新文件`);
    } catch (error) {
      setFileError(error instanceof Error ? error.message : "更新文件生成失败");
    } finally {
      setDownloading(false);
    }
  };

  const handleFileChange = (nextFile: File | null) => {
    if (nextFile && !nextFile.name.toLowerCase().endsWith(".xlsx")) {
      setFile(null);
      setFileError("已有产品更新只接受 .xlsx 文件");
      return;
    }
    setFile(nextFile);
    setFileError(null);
  };

  const validateFile = async () => {
    if (!file) return;
    setValidating(true);
    setFileError(null);
    dispatch({ type: "upload_started" });
    let uploadedBatch: WorkflowBatch | null = null;
    try {
      await cancelOpenBatch();
      const uploaded = await uploadProductWorkbook(file);
      uploadedBatch = uploaded;
      const checked = await validateProductBatch(uploaded.id);
      const view = checked.can_continue ? "changes" : "issues";
      const rows = await loadAllProductBatchRows(checked.id, view, true);
      dispatch({
        type: checked.can_continue ? "validation_succeeded" : "validation_failed",
        batch: checked,
        rows: rows.items,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "文件检查失败";
      dispatch({ type: "validation_request_failed", error: message, batch: uploadedBatch });
    } finally {
      setValidating(false);
    }
  };

  const retryValidation = async () => {
    if (!workflow.batch) return;
    setValidating(true);
    try {
      const checked = await validateProductBatch(workflow.batch.id);
      const view = checked.can_continue ? "changes" : "issues";
      const rows = await loadAllProductBatchRows(checked.id, view, true);
      dispatch({
        type: checked.can_continue ? "validation_succeeded" : "validation_failed",
        batch: checked,
        rows: rows.items,
      });
    } catch (error) {
      dispatch({ type: "validation_request_failed", error: error instanceof Error ? error.message : "重新检查失败" });
    } finally {
      setValidating(false);
    }
  };

  const commit = async () => {
    if (!workflow.batch) return;
    setCommitting(true);
    try {
      const result = await commitProductBatch(workflow.batch.id);
      dispatch({ type: "commit_succeeded", result });
      setConfirmOpen(false);
    } catch (error) {
      dispatch({ type: "commit_failed", error: error instanceof Error ? error.message : "提交失败" });
      setConfirmOpen(false);
    } finally {
      setCommitting(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto bg-muted/20">
      <div className="mx-auto max-w-7xl px-6 py-6">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <button className="mb-2 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground" onClick={() => router.push("/dashboard/workbench")}>
              <ArrowLeft className="size-3.5" />返回工作台
            </button>
            <h1 className="text-lg font-semibold">已有产品更新</h1>
            <p className="mt-1 text-xs text-muted-foreground">先从数据库选择产品，再下载带系统标识的文件；检查和核对完成前不会写入数据。</p>
          </div>
          {(workflow.stage > 1 || Object.keys(workflow.selectedProducts).length > 0) && (
            <Button size="sm" variant="outline" disabled={validating || committing} onClick={() => void reset()}><RefreshCw />重新开始</Button>
          )}
        </div>

        <Card className="gap-0 overflow-hidden rounded-md py-0 shadow-sm">
          <WorkflowStepBar labels={STEPS} current={workflow.stage} />
          <CardContent className="p-6">
            {workflow.stage === 1 && (
              <ProductSelectionStep
                products={products}
                total={total}
                page={page}
                pageSize={PAGE_SIZE}
                selectedProducts={workflow.selectedProducts}
                filters={filters}
                categories={categories}
                suppliers={suppliers}
                countries={countries}
                ports={ports}
                loading={productsLoading}
                error={productsError}
                onFiltersChange={(nextFilters) => { setFilters(nextFilters); setPage(0); }}
                onSelectProduct={(product, selected) => dispatch({ type: "select_product", product, selected })}
                onSelectVisible={(visibleProducts, selected) => visibleProducts.forEach((product) => dispatch({ type: "select_product", product, selected }))}
                onClearSelection={() => dispatch({ type: "clear_products" })}
                onPageChange={setPage}
                onRetry={() => void loadProducts()}
                onNext={() => dispatch({ type: "continue_products" })}
              />
            )}

            {workflow.stage === 2 && (
              <UpdateScopeStep
                selectedCount={Object.keys(workflow.selectedProducts).length}
                scope={workflow.scope}
                onScopeChange={(scope) => dispatch({ type: "set_scope", scope })}
                onBack={() => dispatch({ type: "go_back", stage: 1 })}
                onNext={() => dispatch({ type: "continue_scope" })}
              />
            )}

            {workflow.stage === 3 && (
              <WorkbookStep
                selectedCount={Object.keys(workflow.selectedProducts).length}
                scope={workflow.scope}
                fileName={file?.name ?? null}
                downloading={downloading}
                validating={validating}
                error={fileError || workflow.error}
                onDownload={() => void handleDownload()}
                onFileChange={handleFileChange}
                onBack={() => dispatch({ type: "go_back", stage: 2 })}
                onValidate={() => void validateFile()}
              />
            )}

            {workflow.stage === 4 && (
              <ValidationStep
                batch={workflow.batch}
                rows={workflow.rows}
                validating={validating}
                error={workflow.error}
                onBack={() => dispatch({ type: "go_back", stage: 3 })}
                onRetry={() => void retryValidation()}
                onContinue={() => dispatch({ type: "go_back", stage: 5 })}
              />
            )}

            {workflow.stage === 5 && workflow.completed && workflow.result && (
              <CompletionStep result={workflow.result} onReset={() => void reset()} />
            )}

            {workflow.stage === 5 && !workflow.completed && workflow.batch && (
              <ReviewStep
                batch={workflow.batch}
                rows={workflow.rows}
                committing={committing}
                error={workflow.error}
                confirmOpen={confirmOpen}
                onConfirmOpenChange={setConfirmOpen}
                onBack={() => dispatch({ type: "go_back", stage: 4 })}
                onCommit={() => void commit()}
              />
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
