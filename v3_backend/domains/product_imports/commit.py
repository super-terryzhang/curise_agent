"""One-transaction commit and guarded rollback for staged product imports."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domains.dynamic_data.models import DataRecord, DataTable
from domains.masterdata.models import Product, ProductPricePeriod
from domains.masterdata.price_periods import (
    create_period_in_transaction,
    update_period_in_transaction,
)
from domains.masterdata.schemas import ProductPricePeriodCreate, ProductPricePeriodUpdate
from scripts.seed_clean_product_fields import PRODUCT_TABLE_ID

from .contracts import CommitResult, RollbackResult
from .models import ImportBatch, ImportChange, ImportRow, utc_now


class ImportConflict(RuntimeError):
    """The preview no longer represents current database state."""


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


PRODUCT_FIELDS = (
    "product_name_en",
    "code",
    "country_id",
    "category_id",
    "supplier_id",
    "port_id",
    "unit",
    "brand",
    "status",
)


def _product_state(row: Product) -> dict[str, Any]:
    return {field: _json_value(getattr(row, field)) for field in PRODUCT_FIELDS} | {
        "revision": row.revision,
        "price_version": row.price_version,
    }


def _period_state(row: ProductPricePeriod) -> dict[str, Any]:
    return {
        "product_id": row.product_id,
        "price_type": row.price_type,
        "amount": str(row.amount),
        "currency": row.currency,
        "effective_from": row.effective_from.isoformat(),
        "effective_to": row.effective_to.isoformat(),
        "status": row.status,
        "source": row.source,
        "source_batch_id": row.source_batch_id,
        "created_by": row.created_by,
        "updated_by": row.updated_by,
        "revision": row.revision,
    }


def _extension_state(row: DataRecord) -> dict[str, Any]:
    return {
        "values": _json_value(row.values),
        "revision": row.revision,
        "schema_version": row.schema_version,
        "status": row.status,
        "updated_by": row.updated_by,
    }


def _new_change(
    batch_id: UUID,
    sequence: int,
    entity_type: str,
    entity_id: Any,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    revision: int | None,
) -> ImportChange:
    return ImportChange(
        batch_id=batch_id,
        sequence=sequence,
        entity_type=entity_type,
        entity_id=str(entity_id),
        action=action,
        before=_json_value(before),
        after=_json_value(after),
        expected_after_revision=revision,
    )


def _lock_batch(db: Session, batch_id: UUID, user_id: int) -> ImportBatch:
    batch = db.scalar(
        select(ImportBatch).where(ImportBatch.id == batch_id).with_for_update()
    )
    if batch is None or batch.user_id != user_id:
        raise ValueError("导入批次不存在")
    return batch


def _verify_snapshot(db: Session, row: ImportRow, table: DataTable) -> None:
    if row.snapshot.get("schema_version") not in (None, table.schema_version):
        raise ImportConflict("产品字段结构已变化，请重新上传")
    if row.target_product_id is not None and row.sheet_key == "products":
        product = db.get(Product, row.target_product_id)
        if product is None or product.revision != row.snapshot.get("product_revision"):
            raise ImportConflict("产品已变化，请重新检查后提交")
        anchor = db.scalar(
            select(DataRecord).where(
                DataRecord.table_id == PRODUCT_TABLE_ID,
                DataRecord.source_record_id == str(product.id),
                DataRecord.status == "active",
            )
        )
        current_revision = anchor.revision if anchor else 0
        if current_revision != row.snapshot.get("extension_revision", 0):
            raise ImportConflict("产品扩展信息已变化，请重新检查后提交")
    if row.target_period_id is not None:
        period = db.get(ProductPricePeriod, row.target_period_id)
        if period is None or period.revision != row.snapshot.get("period_revision"):
            raise ImportConflict("价格区间已变化，请重新检查后提交")


def _apply_product_row(
    db: Session,
    batch: ImportBatch,
    row: ImportRow,
    user_id: int,
    sequence: int,
    product_by_key: dict[tuple[str, int], Product],
) -> tuple[int, int, list[ImportChange]]:
    core = dict(row.normalized_values["core_values"])
    extensions = dict(row.normalized_values.get("extension_values") or {})
    changes: list[ImportChange] = []
    if row.action == "create":
        product = Product(**core)
        db.add(product)
        db.flush()
        product_change = _new_change(
            batch.id, sequence, "product", product.id, "create", None, _product_state(product), product.revision
        )
        db.add(product_change)
        changes.append(product_change)
        sequence += 1
    else:
        product = db.get(Product, row.target_product_id)
        assert product is not None
        before = _product_state(product)
        for field, value in core.items():
            setattr(product, field, value)
        db.flush()
        product_change = _new_change(
            batch.id, sequence, "product", product.id, "update", before, _product_state(product), product.revision
        )
        db.add(product_change)
        changes.append(product_change)
        sequence += 1

    key = (row.product_code_normalized, row.port_id)
    product_by_key[key] = product
    anchor = db.scalar(
        select(DataRecord).where(
            DataRecord.table_id == PRODUCT_TABLE_ID,
            DataRecord.source_record_id == str(product.id),
            DataRecord.status == "active",
        )
    )
    if extensions:
        if anchor is None:
            anchor = DataRecord(
                table_id=PRODUCT_TABLE_ID,
                source_record_id=str(product.id),
                values=extensions,
                revision=1,
                schema_version=batch.product_schema_version,
                status="active",
                created_by=user_id,
                updated_by=user_id,
            )
            db.add(anchor)
            db.flush()
            extension_change = _new_change(
                batch.id,
                sequence,
                "extension",
                anchor.id,
                "create",
                None,
                _extension_state(anchor),
                anchor.revision,
            )
            db.add(extension_change)
            changes.append(extension_change)
            sequence += 1
        elif anchor.values != extensions:
            before = _extension_state(anchor)
            anchor.values = extensions
            anchor.revision += 1
            anchor.schema_version = batch.product_schema_version
            anchor.updated_by = user_id
            anchor.updated_at = utc_now()
            db.flush()
            extension_change = _new_change(
                batch.id,
                sequence,
                "extension",
                anchor.id,
                "update",
                before,
                _extension_state(anchor),
                anchor.revision,
            )
            db.add(extension_change)
            changes.append(extension_change)
            sequence += 1
    return sequence, 1, changes


def _apply_price_row(
    db: Session,
    batch: ImportBatch,
    row: ImportRow,
    user_id: int,
    sequence: int,
    product_by_key: dict[tuple[str, int], Product],
) -> tuple[int, ImportChange]:
    values = row.normalized_values
    key = (row.product_code_normalized, row.port_id)
    product = product_by_key.get(key)
    if product is None and row.target_product_id is not None:
        product = db.get(Product, row.target_product_id)
    if product is None:
        raise ImportConflict("价格记录对应的产品已不存在")
    if row.action == "create":
        period = create_period_in_transaction(
            db,
            product.id,
            ProductPricePeriodCreate(
                price_type=values["price_type"],
                amount=Decimal(values["amount"]),
                currency=values.get("currency"),
                effective_from=date.fromisoformat(values["effective_from"]),
                effective_to=date.fromisoformat(values["effective_to"]),
            ),
            actor_id=user_id,
            source="product_import",
            bump_product_version=False,
        )
        change = _new_change(
            batch.id, sequence, "price_period", period.id, "create", None, _period_state(period), period.revision
        )
    else:
        period = db.get(ProductPricePeriod, row.target_period_id)
        assert period is not None
        before = _period_state(period)
        period = update_period_in_transaction(
            db,
            product.id,
            period.id,
            ProductPricePeriodUpdate(
                amount=Decimal(values["amount"]),
                currency=values.get("currency"),
                effective_from=date.fromisoformat(values["effective_from"]),
                effective_to=date.fromisoformat(values["effective_to"]),
            ),
            actor_id=user_id,
            bump_product_version=False,
        )
        change = _new_change(
            batch.id, sequence, "price_period", period.id, "update", before, _period_state(period), period.revision
        )
    db.add(change)
    return sequence + 1, change


def commit_batch(db: Session, batch_id: UUID, user_id: int) -> CommitResult:
    try:
        batch = _lock_batch(db, batch_id, user_id)
        if batch.status == "committed":
            return CommitResult.model_validate((batch.result or {})["commit"])
        if batch.status != "ready" or batch.block_count:
            raise ImportConflict("只有检查通过的批次才能提交")
        table = db.scalar(
            select(DataTable).where(DataTable.id == PRODUCT_TABLE_ID).with_for_update()
        )
        if table is None or table.schema_version != batch.product_schema_version:
            raise ImportConflict("产品字段结构已变化，请重新上传")
        rows = list(
            db.scalars(
                select(ImportRow)
                .where(ImportRow.batch_id == batch.id)
                .order_by(ImportRow.sheet_key, ImportRow.source_row_number)
                .with_for_update()
            )
        )
        if any(row.action == "block" for row in rows):
            raise ImportConflict("批次仍包含阻止项")
        product_ids = sorted(
            {row.target_product_id for row in rows if row.target_product_id is not None}
        )
        if product_ids:
            list(
                db.scalars(
                    select(Product)
                    .where(Product.id.in_(product_ids))
                    .order_by(Product.id)
                    .with_for_update()
                )
            )
        period_ids = sorted(
            {row.target_period_id for row in rows if row.target_period_id is not None}
        )
        if period_ids:
            list(
                db.scalars(
                    select(ProductPricePeriod)
                    .where(ProductPricePeriod.id.in_(period_ids))
                    .order_by(ProductPricePeriod.id)
                    .with_for_update()
                )
            )
        for row in rows:
            _verify_snapshot(db, row, table)

        sequence = 1
        created = updated = skipped = 0
        product_by_key: dict[tuple[str, int], Product] = {}
        product_changes: dict[int, ImportChange] = {}
        price_product_before: dict[int, dict[str, Any]] = {}
        for row in [item for item in rows if item.sheet_key == "products"]:
            if row.action == "skip":
                skipped += 1
                continue
            sequence, _, changes = _apply_product_row(
                db, batch, row, user_id, sequence, product_by_key
            )
            for change in changes:
                if change.entity_type == "product":
                    product_changes[int(change.entity_id)] = change
            created += row.action == "create"
            updated += row.action == "update"
        for row in [item for item in rows if item.sheet_key == "prices"]:
            if row.action == "skip":
                skipped += 1
                continue
            key = (row.product_code_normalized, row.port_id)
            price_product = product_by_key.get(key)
            if price_product is None and row.target_product_id is not None:
                price_product = db.get(Product, row.target_product_id)
            if price_product is not None and price_product.id not in price_product_before:
                price_product_before[price_product.id] = _product_state(price_product)
            sequence, _ = _apply_price_row(
                db, batch, row, user_id, sequence, product_by_key
            )
            created += row.action == "create"
            updated += row.action == "update"
        for product_id in sorted(price_product_before):
            product = db.get(Product, product_id)
            product.price_version += 1
        db.flush()
        for product_id, before in price_product_before.items():
            product = db.get(Product, product_id)
            if product_id not in product_changes:
                change = _new_change(
                    batch.id,
                    sequence,
                    "product",
                    product.id,
                    "update",
                    before,
                    _product_state(product),
                    product.revision,
                )
                db.add(change)
                product_changes[product_id] = change
                sequence += 1
        db.flush()
        for product_id, change in product_changes.items():
            product = db.get(Product, product_id)
            change.after = _product_state(product)
            change.expected_after_revision = product.revision
        result = CommitResult(
            batch_id=batch.id,
            created=int(created),
            updated=int(updated),
            skipped=skipped,
        )
        batch.status = "committed"
        batch.committed_at = utc_now()
        batch.result = {**(batch.result or {}), "commit": result.model_dump(mode="json")}
        db.commit()
        return result
    except IntegrityError as exc:
        db.rollback()
        raise ImportConflict("数据已被其他操作更新，请重新检查后提交") from exc
    except Exception:
        db.rollback()
        raise


def _restore_product(row: Product, state: dict[str, Any]) -> None:
    for field in PRODUCT_FIELDS:
        setattr(row, field, state.get(field))
    row.price_version = state.get("price_version", row.price_version)


def _restore_period(row: ProductPricePeriod, state: dict[str, Any]) -> None:
    row.product_id = state["product_id"]
    row.price_type = state["price_type"]
    row.amount = Decimal(state["amount"])
    row.currency = state.get("currency")
    row.effective_from = date.fromisoformat(state["effective_from"])
    row.effective_to = date.fromisoformat(state["effective_to"])
    row.status = state["status"]
    row.source = state["source"]
    row.source_batch_id = state.get("source_batch_id")
    row.created_by = state.get("created_by")
    row.updated_by = state.get("updated_by")


def rollback_batch(db: Session, batch_id: UUID, user_id: int) -> RollbackResult:
    try:
        batch = _lock_batch(db, batch_id, user_id)
        if batch.status == "rolled_back":
            return RollbackResult.model_validate((batch.result or {})["rollback"])
        if batch.status != "committed":
            raise ImportConflict("只能回滚已提交的批次")
        changes = list(
            db.scalars(
                select(ImportChange)
                .where(ImportChange.batch_id == batch.id)
                .order_by(ImportChange.sequence.desc())
                .with_for_update()
            )
        )
        # Verify every dependency before changing any row.
        for change in changes:
            if change.entity_type == "product":
                entity = db.get(Product, int(change.entity_id))
            elif change.entity_type == "extension":
                entity = db.get(DataRecord, UUID(change.entity_id))
            else:
                entity = db.get(ProductPricePeriod, int(change.entity_id))
            if entity is None or entity.revision != change.expected_after_revision:
                raise ImportConflict("导入后数据已有后续修改，不能自动回滚")

        restored = archived = 0
        for change in changes:
            if change.entity_type == "price_period":
                entity = db.get(ProductPricePeriod, int(change.entity_id))
                if change.action == "create":
                    entity.status = False
                    archived += 1
                else:
                    _restore_period(entity, change.before or {})
                    restored += 1
            elif change.entity_type == "extension":
                entity = db.get(DataRecord, UUID(change.entity_id))
                if change.action == "create":
                    entity.status = "archived"
                    entity.revision += 1
                    archived += 1
                else:
                    before = change.before or {}
                    entity.values = before.get("values", {})
                    entity.schema_version = before.get("schema_version", entity.schema_version)
                    entity.status = before.get("status", "active")
                    entity.updated_by = before.get("updated_by", entity.updated_by)
                    entity.revision += 1
                    restored += 1
            else:
                entity = db.get(Product, int(change.entity_id))
                if change.action == "create":
                    entity.status = False
                    archived += 1
                else:
                    _restore_product(entity, change.before or {})
                    restored += 1
        result = RollbackResult(
            batch_id=batch.id, restored=restored, archived=archived
        )
        batch.status = "rolled_back"
        batch.rolled_back_at = utc_now()
        batch.result = {**(batch.result or {}), "rollback": result.model_dump(mode="json")}
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
