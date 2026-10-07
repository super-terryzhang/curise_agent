"""Public application service for the temporary product importer."""

from __future__ import annotations

from math import ceil
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from domains.dynamic_data.models import DataField, DataRecord, DataTable
from domains.masterdata.models import Category, Country, Port, Product, ProductPricePeriod, Supplier
from domains.masterdata.price_periods import serialize as serialize_period
from scripts.seed_clean_product_fields import PRODUCT_TABLE_ID

from .commit import commit_batch, rollback_batch
from .contracts import ImportIssue
from .models import ImportBatch, ImportRow
from .parser import parse_workbook
from .template import build_product_workbook
from .validation import validate_batch


def batch_summary(batch: ImportBatch) -> dict:
    return {
        "id": str(batch.id),
        "filename": batch.filename,
        "status": batch.status,
        "total_rows": batch.total_rows,
        "counts": {
            "create": batch.create_count,
            "update": batch.update_count,
            "skip": batch.skip_count,
            "warning": batch.warning_count,
            "block": batch.block_count,
        },
        "can_commit": batch.status == "ready" and batch.block_count == 0,
        "created_at": batch.created_at,
        "validated_at": batch.validated_at,
        "committed_at": batch.committed_at,
        "rolled_back_at": batch.rolled_back_at,
    }


def setup_status(db: Session, *, database_name: str) -> dict:
    table = db.get(DataTable, PRODUCT_TABLE_ID)
    return {
        "enabled": True,
        "mode": "temporary",
        "database_name": database_name,
        "schema_version": table.schema_version if table else None,
        "field_count": db.scalar(
            select(func.count())
            .select_from(DataField)
            .where(DataField.table_id == PRODUCT_TABLE_ID, DataField.status == "active")
        )
        or 0,
        "product_count": db.scalar(select(func.count()).select_from(Product)) or 0,
        "price_period_count": db.scalar(
            select(func.count()).select_from(ProductPricePeriod)
        )
        or 0,
    }


def list_batch_rows(
    db: Session, batch_id: UUID, user_id: int, *, page: int, page_size: int
) -> dict:
    batch = db.get(ImportBatch, batch_id)
    if batch is None or batch.user_id != user_id:
        raise ValueError("导入批次不存在")
    total = db.scalar(
        select(func.count()).select_from(ImportRow).where(ImportRow.batch_id == batch.id)
    ) or 0
    rows = list(
        db.scalars(
            select(ImportRow)
            .where(ImportRow.batch_id == batch.id)
            .order_by(ImportRow.sheet_key, ImportRow.source_row_number)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    table = db.get(DataTable, PRODUCT_TABLE_ID)
    field_by_id = {
        str(field.id): field
        for field in db.scalars(
            select(DataField).where(DataField.table_id == PRODUCT_TABLE_ID)
        )
    }
    schema_current = bool(table and table.schema_version == batch.product_schema_version)
    global_issues = list((batch.result or {}).get("issues", []))
    if not schema_current and not any(item.get("code") == "STALE_SCHEMA" for item in global_issues):
        global_issues.append(
            ImportIssue(
                severity="block",
                code="STALE_SCHEMA",
                message="产品字段结构已变化，请下载最新模板",
            ).model_dump(mode="json")
        )
    return {
        **batch_summary(batch),
        "can_commit": batch.status == "ready" and batch.block_count == 0 and schema_current,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": ceil(total / page_size) if total else 0,
        "issues": global_issues,
        "items": [
            {
                "id": str(row.id),
                "sheet": row.sheet_key,
                "row": row.source_row_number,
                "action": row.action,
                "product_code": row.product_code_normalized,
                "port_id": row.port_id,
                "before_values": _public_values(row.snapshot.get("before"), field_by_id),
                "normalized_values": _public_values(row.normalized_values, field_by_id),
                "issues": row.issues,
            }
            for row in rows
        ],
    }


def _public_values(raw: dict | None, field_by_id: dict[str, DataField]) -> dict | None:
    if raw is None:
        return None
    values = dict(raw)
    extensions = values.pop("extension_values", None)
    if extensions is not None:
        values["extensions"] = [
            {
                "label": field_by_id[field_id].label,
                "type": field_by_id[field_id].field_type,
                "value": value,
            }
            for field_id, value in extensions.items()
            if field_id in field_by_id
        ]
    return values


def list_products(db: Session, *, q: str | None, page: int, page_size: int) -> dict:
    filters = [Product.status.is_(True)]
    if q:
        pattern = f"%{q.strip()}%"
        filters.append(
            or_(Product.code.ilike(pattern), Product.product_name_en.ilike(pattern))
        )
    total = db.scalar(select(func.count()).select_from(Product).where(*filters)) or 0
    rows = list(
        db.scalars(
            select(Product)
            .where(*filters)
            .order_by(Product.code, Product.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": ceil(total / page_size) if total else 0,
        "items": [_product_summary(db, row) for row in rows],
    }


def _name(db: Session, model, row_id: int | None) -> str | None:
    row = db.get(model, row_id) if row_id is not None else None
    return row.name if row else None


def _product_summary(db: Session, product: Product) -> dict:
    fields = list(
        db.scalars(
            select(DataField)
            .where(DataField.table_id == PRODUCT_TABLE_ID, DataField.status == "active")
            .order_by(DataField.sort_order, DataField.id)
        )
    )
    anchor = db.scalar(
        select(DataRecord).where(
            DataRecord.table_id == PRODUCT_TABLE_ID,
            DataRecord.source_record_id == str(product.id),
            DataRecord.status == "active",
        )
    )
    extension_values = anchor.values if anchor else {}
    return {
        "id": product.id,
        "code": product.code,
        "name": product.product_name_en,
        "port": _name(db, Port, product.port_id),
        "country": _name(db, Country, product.country_id),
        "supplier": _name(db, Supplier, product.supplier_id),
        "unit": product.unit,
        "category": _name(db, Category, product.category_id),
        "brand": product.brand,
        "status": product.status,
        "revision": product.revision,
        "extensions": [
            {
                "label": field.label,
                "type": field.field_type,
                "value": extension_values.get(str(field.id)),
            }
            for field in fields
        ],
    }


def get_product(db: Session, product_id: int) -> dict:
    product = db.get(Product, product_id)
    if product is None:
        raise ValueError("产品不存在")
    table = db.get(DataTable, PRODUCT_TABLE_ID)
    fields = list(
        db.scalars(
            select(DataField)
            .where(DataField.table_id == PRODUCT_TABLE_ID, DataField.status == "active")
            .order_by(DataField.sort_order, DataField.id)
        )
    )
    anchor = db.scalar(
        select(DataRecord).where(
            DataRecord.table_id == PRODUCT_TABLE_ID,
            DataRecord.source_record_id == str(product.id),
            DataRecord.status == "active",
        )
    )
    values = anchor.values if anchor else {}
    periods = list(
        db.scalars(
            select(ProductPricePeriod)
            .where(ProductPricePeriod.product_id == product.id)
            .order_by(
                ProductPricePeriod.price_type,
                ProductPricePeriod.effective_from,
                ProductPricePeriod.id,
            )
        )
    )
    return {
        **_product_summary(db, product),
        "schema_version": table.schema_version if table else None,
        "extensions": [
            {
                "label": field.label,
                "type": field.field_type,
                "value": values.get(str(field.id)),
            }
            for field in fields
        ],
        "price_periods": [serialize_period(period) for period in periods],
    }

__all__ = [
    "build_product_workbook",
    "commit_batch",
    "parse_workbook",
    "rollback_batch",
    "validate_batch",
    "batch_summary",
    "get_product",
    "list_batch_rows",
    "list_products",
    "setup_status",
]
