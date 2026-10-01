import { fetchWithAuth } from "./fetch-with-auth";
import type { DirectUpdateRequest } from "./product-batch-edit";
import {
  commitProductBatch,
  type CommitResult,
  type WorkflowBatch,
} from "./product-upload-api";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";
export async function prepareDirectProductUpdate(
  request: DirectUpdateRequest,
): Promise<WorkflowBatch> {
  const response = await fetchWithAuth(
    `${API_BASE}/api/data-upload/workbench/products/prepare-update`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      timeout: 120000,
    },
  );
  const body = await response.json();
  if (!response.ok)
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : "更新内容不符合要求，请检查填写内容",
    );
  return body as WorkflowBatch;
}

export async function getDirectProductUpdate(
  batchId: number,
): Promise<WorkflowBatch> {
  const response = await fetchWithAuth(
    `${API_BASE}/api/data-upload/workbench/batches/${batchId}`,
  );
  if (!response.ok)
    throw new Error("暂时无法读取批次状态，请重试或查看最近处理记录");
  return response.json() as Promise<WorkflowBatch>;
}
export function completedDirectResult(batch: WorkflowBatch): CommitResult {
  return {
    created: batch.summary.create,
    updated: batch.summary.update,
    skipped: batch.summary.skip,
    errors: batch.summary.error,
    error_details: [],
  };
}
export async function commitDirectProductUpdate(
  batchId: number,
): Promise<CommitResult> {
  try {
    return await commitProductBatch(batchId);
  } catch (error) {
    // The write may have succeeded before a timeout or a lost HTTP response.
    // Read status instead of blindly repeating the write or claiming failure.
    let batch: WorkflowBatch;
    try {
      batch = await getDirectProductUpdate(batchId);
    } catch {
      throw new Error(
        "保存结果暂时无法确认，请稍后重试或查看最近处理记录；请勿重新创建同一批变更",
      );
    }
    if (batch.status === "completed") return completedDirectResult(batch);
    throw error;
  }
}
