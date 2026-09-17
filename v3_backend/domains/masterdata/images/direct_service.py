"""Direct, recoverable product-image workbench workflow.

Unlike the legacy ZIP path, every source image is staged under its own
storage key.  The database remains the source of truth for validation,
review order and idempotent commit state.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from domains.masterdata import _product_images_service as image_service
from domains.masterdata.images import ImageProcessingError, generate_thumbnails
from domains.masterdata.images.bulk_models import (
    BulkImageBatch,
    BulkImageProductPlan,
    BulkImageStaging,
)
from domains.masterdata.images.bulk_service import BadRequest, NotFound, StatusConflict
from domains.masterdata.models import Product, ProductImage
from domains.masterdata.schemas import ProductImageReorderEntry
from infrastructure.storage import get_storage

ACTIVE_STATUSES = ("uploading", "preview_ready", "processing")


def create_batch(db: Session, *, user_id: int) -> dict[str, Any]:
    existing = db.execute(
        select(BulkImageBatch)
        .where(BulkImageBatch.user_id == user_id)
        .where(BulkImageBatch.status.in_(ACTIVE_STATUSES))
        .order_by(BulkImageBatch.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        if existing.source_type == "direct":
            return serialize_batch(db, existing)
        raise BadRequest(f"你已有一个未完成的图片批次（#{existing.id}），请先完成或取消")

    batch = BulkImageBatch(
        user_id=user_id,
        zip_filename=f"图片上传 {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
        source_type="direct",
        status="uploading",
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return serialize_batch(db, batch)


def upload_file(
    db: Session,
    *,
    batch_id: int,
    user_id: int,
    is_admin: bool,
    filename: str,
    content: bytes,
    content_type: str,
    product_id: int | None,
) -> dict[str, Any]:
    batch = _load_direct_batch(db, batch_id, user_id, is_admin)
    if batch.status not in ("uploading", "preview_ready"):
        raise StatusConflict(f"无法向处于 {batch.status} 状态的批次添加图片")
    if not filename:
        raise BadRequest("图片文件名不能为空")

    order = db.scalar(
        select(func.coalesce(func.max(BulkImageStaging.upload_order), 0)).where(
            BulkImageStaging.batch_id == batch.id
        )
    ) or 0
    row = BulkImageStaging(
        batch_id=batch.id,
        zip_path=filename,
        image_filename=Path(filename).name[:255],
        file_size_bytes=len(content),
        product_id=product_id,
        status="checking",
        content_type=(content_type or "application/octet-stream")[:100],
        upload_order=order + 1,
        decision="include",
    )
    db.add(row)
    db.flush()

    issue_code, message = _file_issue(content, content_type)
    if product_id is not None and db.get(Product, product_id) is None:
        issue_code, message = "product_not_found", "选择的产品不存在，请重新分配"
        row.product_id = None

    if issue_code is None:
        bundle = generate_thumbnails(content)
        storage = get_storage()
        token = uuid.uuid4().hex
        folder = f"bulk-image-staging/{batch.id}"
        row.storage_key = storage.upload(
            f"{folder}/original",
            f"{token}-{Path(filename).name}",
            content,
            content_type or "application/octet-stream",
        )
        row.preview_storage_key = storage.upload(
            f"{folder}/preview", f"{token}.jpg", bundle.medium, "image/jpeg"
        )

    row.issue_code = issue_code
    row.error_message = message
    row.status = "ready" if issue_code is None and product_id is not None else "needs_attention"
    if issue_code is None and product_id is None:
        row.issue_code = "product_required"
        row.error_message = "尚未指定产品，请选择这张图片属于哪个产品"

    batch.status = "uploading"
    _refresh_counters(db, batch)
    db.commit()
    db.refresh(row)
    return serialize_row(row)


def validate_batch(
    db: Session, *, batch_id: int, user_id: int, is_admin: bool
) -> dict[str, Any]:
    batch = _load_direct_batch(db, batch_id, user_id, is_admin)
    if batch.status not in ("uploading", "preview_ready"):
        raise StatusConflict(f"无法检查处于 {batch.status} 状态的批次")

    rows = list(
        db.execute(
            select(BulkImageStaging)
            .where(BulkImageStaging.batch_id == batch.id)
            .order_by(BulkImageStaging.upload_order, BulkImageStaging.id)
        ).scalars()
    )
    included_by_product: dict[int, list[BulkImageStaging]] = {}
    for row in rows:
        if row.decision == "exclude":
            row.status = "excluded"
            row.issue_code = None
            row.error_message = None
            continue
        if row.storage_key is None:
            row.status = "needs_attention"
            if row.issue_code is None:
                row.issue_code = "invalid_image"
                row.error_message = "图片没有通过文件检查，请替换或排除"
            continue
        if row.product_id is None or db.get(Product, row.product_id) is None:
            row.product_id = None
            row.status = "needs_attention"
            row.issue_code = "product_required"
            row.error_message = "尚未指定产品，请选择这张图片属于哪个产品"
            continue
        included_by_product.setdefault(row.product_id, []).append(row)

    for product_id, product_rows in included_by_product.items():
        existing_count = db.scalar(
            select(func.count(ProductImage.id)).where(ProductImage.product_id == product_id)
        ) or 0
        available = max(0, image_service.MAX_IMAGES_PER_PRODUCT - existing_count)
        for index, row in enumerate(product_rows):
            if index >= available:
                row.status = "needs_attention"
                row.issue_code = "capacity_exceeded"
                row.error_message = (
                    f"该产品已有 {existing_count} 张图片，本批次最多还能加入 {available} 张"
                )
            else:
                row.status = "ready"
                row.issue_code = None
                row.error_message = None

    _rebuild_default_plans(db, batch, rows)
    _refresh_counters(db, batch)
    batch.status = "preview_ready"
    db.commit()
    db.refresh(batch)
    return serialize_batch(db, batch)


def update_row(
    db: Session,
    *,
    batch_id: int,
    row_id: int,
    user_id: int,
    is_admin: bool,
    product_id: int | None = None,
    decision: str | None = None,
) -> dict[str, Any]:
    batch = _load_direct_batch(db, batch_id, user_id, is_admin)
    if batch.status not in ("uploading", "preview_ready"):
        raise StatusConflict("当前批次不能再修改")
    row = db.get(BulkImageStaging, row_id)
    if row is None or row.batch_id != batch.id:
        raise NotFound("图片行不存在")
    if decision is not None:
        if decision not in ("include", "exclude"):
            raise BadRequest("decision 只能是 include 或 exclude")
        row.decision = decision
    if product_id is not None:
        if db.get(Product, product_id) is None:
            raise BadRequest("选择的产品不存在")
        row.product_id = product_id
        row.decision = "include"
    batch.status = "uploading"
    db.commit()
    return validate_batch(db, batch_id=batch.id, user_id=user_id, is_admin=is_admin)


def save_plan(
    db: Session,
    *,
    batch_id: int,
    product_id: int,
    user_id: int,
    is_admin: bool,
    items: list[str],
) -> dict[str, Any]:
    batch = _load_direct_batch(db, batch_id, user_id, is_admin)
    if batch.status != "preview_ready":
        raise StatusConflict("请先完成程序检查，再调整主图和显示顺序")
    plan = db.execute(
        select(BulkImageProductPlan).where(
            BulkImageProductPlan.batch_id == batch.id,
            BulkImageProductPlan.product_id == product_id,
        )
    ).scalar_one_or_none()
    if plan is None:
        raise NotFound("该产品不在当前批次中")
    expected = set(json.loads(plan.ordered_items))
    if len(items) != len(set(items)) or set(items) != expected:
        raise BadRequest("排序必须包含该产品的全部现有图片和待上传图片")
    plan.ordered_items = json.dumps(items)
    db.commit()
    return serialize_plan(db, plan)


def get_active(db: Session, *, user_id: int, is_admin: bool) -> dict[str, Any] | None:
    del is_admin  # active recovery is deliberately scoped to the current user
    batch = db.execute(
        select(BulkImageBatch)
        .where(BulkImageBatch.user_id == user_id)
        .where(BulkImageBatch.source_type == "direct")
        .where(BulkImageBatch.status.in_(ACTIVE_STATUSES))
        .order_by(BulkImageBatch.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return serialize_batch(db, batch) if batch is not None else None


def list_history(db: Session, *, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    batches = db.execute(
        select(BulkImageBatch)
        .where(BulkImageBatch.user_id == user_id)
        .where(BulkImageBatch.source_type == "direct")
        .order_by(BulkImageBatch.id.desc())
        .limit(min(max(limit, 1), 100))
    ).scalars()
    return [serialize_batch(db, batch, include_rows=False) for batch in batches]


def run_commit(db: Session, batch: BulkImageBatch) -> None:
    plans = list(
        db.execute(
            select(BulkImageProductPlan)
            .where(BulkImageProductPlan.batch_id == batch.id)
            .order_by(BulkImageProductPlan.id)
        ).scalars()
    )
    if not plans:
        batch.status = "error"
        batch.error_message = "没有可提交的产品图片计划"
        db.commit()
        return

    for plan in plans:
        current_existing = list(
            db.execute(
                select(ProductImage.id)
                .where(ProductImage.product_id == plan.product_id)
                .order_by(ProductImage.display_order, ProductImage.id)
            ).scalars()
        )
        expected_existing = json.loads(plan.expected_existing_image_ids)
        if current_existing != expected_existing:
            batch.status = "error"
            batch.error_message = "产品图片已被其他用户修改，请返回第三步刷新后重新核对"
            db.commit()
            return

        tokens: list[str] = json.loads(plan.ordered_items)
        for token in tokens:
            if not token.startswith("staged:"):
                continue
            row_id = int(token.split(":", 1)[1])
            row = db.get(BulkImageStaging, row_id)
            if row is None or row.batch_id != batch.id or row.decision == "exclude":
                continue
            if row.committed_image_id is not None:
                continue
            try:
                if not row.storage_key:
                    raise ValueError("暂存文件不存在")
                content = get_storage().download(row.storage_key)
                result = image_service.add_product_image(
                    db,
                    product_id=plan.product_id,
                    content=content,
                    filename=row.image_filename,
                    content_type=row.content_type or "application/octet-stream",
                    user_id=batch.user_id,
                )
                row.committed_image_id = result["id"]
                row.status = "committed"
                row.issue_code = None
                row.error_message = None
                batch.ingested_count += 1
            except Exception as exc:  # row-level recovery is intentional
                row.status = "committed_failed"
                row.issue_code = "commit_failed"
                row.error_message = f"写入失败：{str(exc)[:400]}"
                row.retry_count += 1
                batch.failed_count += 1
            db.commit()

        _apply_plan(db, plan)

    batch.status = "completed"
    batch.completed_at = datetime.utcnow()
    batch.error_message = None
    db.commit()
    _cleanup_successful_rows(db, batch.id)


def retry_row(
    db: Session, *, batch_id: int, row_id: int, user_id: int, is_admin: bool
) -> dict[str, Any]:
    batch = _load_direct_batch(db, batch_id, user_id, is_admin)
    row = db.get(BulkImageStaging, row_id)
    if row is None or row.batch_id != batch.id:
        raise NotFound("图片行不存在")
    if row.status != "committed_failed" or row.committed_image_id is not None:
        raise StatusConflict("只有写入失败的图片可以重试")
    if not row.storage_key or row.product_id is None:
        raise BadRequest("暂存文件或产品关联已经不存在，请重新上传")
    try:
        result = image_service.add_product_image(
            db,
            product_id=row.product_id,
            content=get_storage().download(row.storage_key),
            filename=row.image_filename,
            content_type=row.content_type or "application/octet-stream",
            user_id=batch.user_id,
        )
    except Exception as exc:
        row.retry_count += 1
        row.error_message = f"重试失败：{str(exc)[:400]}"
        db.commit()
        return serialize_row(row)
    row.committed_image_id = result["id"]
    row.status = "committed"
    row.issue_code = None
    row.error_message = None
    batch.ingested_count += 1
    batch.failed_count = max(0, batch.failed_count - 1)
    db.commit()
    plan = db.execute(
        select(BulkImageProductPlan).where(
            BulkImageProductPlan.batch_id == batch.id,
            BulkImageProductPlan.product_id == row.product_id,
        )
    ).scalar_one_or_none()
    if plan is not None:
        _apply_plan(db, plan)
    _cleanup_row_storage(row)
    db.commit()
    return serialize_row(row)


def serialize_batch(
    db: Session, batch: BulkImageBatch, *, include_rows: bool = True
) -> dict[str, Any]:
    rows = []
    plans = []
    if include_rows:
        rows = list(
            db.execute(
                select(BulkImageStaging)
                .where(BulkImageStaging.batch_id == batch.id)
                .order_by(BulkImageStaging.upload_order, BulkImageStaging.id)
            ).scalars()
        )
        plans = list(
            db.execute(
                select(BulkImageProductPlan)
                .where(BulkImageProductPlan.batch_id == batch.id)
                .order_by(BulkImageProductPlan.id)
            ).scalars()
        )
    return {
        "id": batch.id,
        "zip_filename": batch.zip_filename,
        "source_type": batch.source_type,
        "status": batch.status,
        "total_files": batch.total_files,
        "matched_count": batch.matched_count,
        "unmatched_count": batch.unmatched_count,
        "error_count": batch.error_count,
        "ingested_count": batch.ingested_count,
        "excluded_count": batch.excluded_count,
        "failed_count": batch.failed_count,
        "error_message": batch.error_message,
        "can_continue": (
            batch.source_type == "direct"
            and batch.status == "preview_ready"
            and batch.total_files > batch.excluded_count
            and batch.error_count == 0
        ),
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "completed_at": batch.completed_at.isoformat() if batch.completed_at else None,
        "rows": [serialize_row(row) for row in rows],
        "plans": [serialize_plan(db, plan) for plan in plans],
    }


def serialize_row(row: BulkImageStaging) -> dict[str, Any]:
    preview_url = None
    if row.preview_storage_key:
        preview_url = get_storage().get_signed_url(row.preview_storage_key, expires_in=3600)
    return {
        "id": row.id,
        "image_filename": row.image_filename,
        "file_size_bytes": row.file_size_bytes,
        "product_id": row.product_id,
        "status": row.status,
        "issue_code": row.issue_code,
        "error_message": row.error_message,
        "upload_order": row.upload_order,
        "decision": row.decision,
        "retry_count": row.retry_count,
        "committed_image_id": row.committed_image_id,
        "preview_url": preview_url,
    }


def serialize_plan(db: Session, plan: BulkImageProductPlan) -> dict[str, Any]:
    product = db.get(Product, plan.product_id)
    existing = list(
        db.execute(
            select(ProductImage)
            .where(ProductImage.product_id == plan.product_id)
            .order_by(ProductImage.display_order, ProductImage.id)
        ).scalars()
    )
    return {
        "product_id": plan.product_id,
        "product_code": product.code if product else None,
        "product_name": product.product_name_en if product else None,
        "expected_existing_image_ids": json.loads(plan.expected_existing_image_ids),
        "ordered_items": json.loads(plan.ordered_items),
        "existing_images": [
            {
                "id": image.id,
                "filename": image.filename,
                "display_order": image.display_order,
                "preview_url": get_storage().get_signed_url(image.medium_key, expires_in=3600),
            }
            for image in existing
        ],
    }


def _file_issue(content: bytes, content_type: str) -> tuple[str | None, str | None]:
    if not content:
        return "empty_file", "图片文件为空，请重新选择"
    if len(content) > image_service.MAX_IMAGE_BYTES:
        return "file_too_large", "图片超过 5MB，请压缩后重新上传"
    normalized = (content_type or "").lower().split(";", 1)[0]
    if normalized not in image_service.ALLOWED_MIME_TYPES:
        return "unsupported_format", "不支持该格式，请使用 JPG、PNG 或 WebP"
    try:
        generate_thumbnails(content)
    except ImageProcessingError as exc:
        return "invalid_image", str(exc)
    return None, None


def _load_direct_batch(
    db: Session, batch_id: int, user_id: int, is_admin: bool
) -> BulkImageBatch:
    batch = db.get(BulkImageBatch, batch_id)
    if batch is None or (not is_admin and batch.user_id != user_id):
        raise NotFound("批次不存在")
    if batch.source_type != "direct":
        raise BadRequest("该批次不是直接图片上传批次")
    return batch


def _refresh_counters(db: Session, batch: BulkImageBatch) -> None:
    db.flush()
    rows = list(
        db.execute(
            select(BulkImageStaging).where(BulkImageStaging.batch_id == batch.id)
        ).scalars()
    )
    batch.total_files = len(rows)
    batch.excluded_count = sum(row.decision == "exclude" for row in rows)
    batch.matched_count = sum(row.status in ("ready", "committed") for row in rows)
    batch.unmatched_count = sum(
        row.decision != "exclude" and row.product_id is None for row in rows
    )
    batch.error_count = sum(row.status == "needs_attention" for row in rows)
    batch.failed_count = sum(row.status == "committed_failed" for row in rows)


def _rebuild_default_plans(
    db: Session, batch: BulkImageBatch, rows: list[BulkImageStaging]
) -> None:
    db.query(BulkImageProductPlan).filter(
        BulkImageProductPlan.batch_id == batch.id
    ).delete(synchronize_session=False)
    product_ids = sorted(
        {
            row.product_id
            for row in rows
            if row.product_id is not None
            and row.decision != "exclude"
            and row.status == "ready"
        }
    )
    for product_id in product_ids:
        existing_ids = list(
            db.execute(
                select(ProductImage.id)
                .where(ProductImage.product_id == product_id)
                .order_by(ProductImage.display_order, ProductImage.id)
            ).scalars()
        )
        staged_ids = [
            row.id
            for row in rows
            if row.product_id == product_id
            and row.decision != "exclude"
            and row.status == "ready"
        ]
        tokens = [f"existing:{image_id}" for image_id in existing_ids]
        tokens.extend(f"staged:{row_id}" for row_id in staged_ids)
        db.add(
            BulkImageProductPlan(
                batch_id=batch.id,
                product_id=product_id,
                expected_existing_image_ids=json.dumps(existing_ids),
                ordered_items=json.dumps(tokens),
            )
        )


def _apply_plan(db: Session, plan: BulkImageProductPlan) -> None:
    rows = {
        row.id: row
        for row in db.execute(
            select(BulkImageStaging).where(BulkImageStaging.batch_id == plan.batch_id)
        ).scalars()
    }
    desired_ids: list[int] = []
    for token in json.loads(plan.ordered_items):
        kind, raw_id = token.split(":", 1)
        if kind == "existing":
            desired_ids.append(int(raw_id))
        elif kind == "staged":
            staged = rows.get(int(raw_id))
            if staged is not None and staged.committed_image_id is not None:
                desired_ids.append(staged.committed_image_id)
    current_ids = list(
        db.execute(
            select(ProductImage.id).where(ProductImage.product_id == plan.product_id)
        ).scalars()
    )
    desired_ids.extend(image_id for image_id in current_ids if image_id not in desired_ids)
    image_service.reorder_product_images(
        db,
        product_id=plan.product_id,
        entries=[
            ProductImageReorderEntry(id=image_id, display_order=index)
            for index, image_id in enumerate(desired_ids)
        ],
    )


def _cleanup_row_storage(row: BulkImageStaging) -> None:
    storage = get_storage()
    for key in (row.storage_key, row.preview_storage_key):
        if key:
            storage.delete(key)
    row.storage_key = None
    row.preview_storage_key = None


def _cleanup_successful_rows(db: Session, batch_id: int) -> None:
    rows = db.execute(
        select(BulkImageStaging).where(
            BulkImageStaging.batch_id == batch_id,
            BulkImageStaging.status == "committed",
        )
    ).scalars()
    for row in rows:
        _cleanup_row_storage(row)
    db.commit()
