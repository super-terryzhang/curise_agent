"""Single-record preparation operations, sharing import and price-period rules."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid5

from sqlalchemy import delete, func, select

from domains.dynamic_data import PRODUCT_TABLE_ID
from domains.dynamic_data import models as dynamic
from domains.dynamic_data.service import lock_tables_in_transaction
from domains.inquiry import Inquiry
from domains.masterdata import models as master
from domains.masterdata import price_periods
from domains.masterdata.schemas import ProductPricePeriodCreate, ProductPricePeriodUpdate
from domains.orders import Order
from infrastructure.db.base import Base

from .catalog import PRICE_FIELDS, load_product_catalog
from .commit import ImportConflict, commit_batch
from .contracts import WorkbookManifest
from .diff import CLEAR_MARKER
from .models import ImportBatch, ImportRow, utc_now
from .validation import validate_batch


class ManualValidation(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("；".join(dict.fromkeys(i["message"] for i in issues)))


def _product(db, product_id, expected_revision=None):
    product = db.scalar(
        select(master.Product).where(master.Product.id == product_id).with_for_update()
    )
    if product is None:
        raise ValueError("产品不存在")
    if expected_revision is not None and product.revision != expected_revision:
        raise ImportConflict("产品已发生变化，请刷新后再操作")
    return product


def edit_config(db, product_id):
    from .service import get_product

    product = _product(db, product_id)
    catalog = load_product_catalog(db)
    detail = get_product(db, product_id)
    fields, values = [], {}
    for field in (*catalog.core_prefix, *catalog.core_suffix):
        value = getattr(product, field.attribute)
        options = []
        if field.relation:
            model = {
                "port": master.Port,
                "country": master.Country,
                "supplier": master.Supplier,
                "category": master.Category,
            }[field.relation]
            choices = list(
                db.scalars(select(model).where(model.status.is_(True)).order_by(model.name))
            )
            if value and all(row.id != value for row in choices):
                current = db.get(model, value)
                if current:
                    choices.append(current)
            options = [{"value": row.name, "label": row.name} for row in choices]
            row = db.get(model, value) if value else None
            value = row.name if row else ""
        if field.key == "status":
            options = [{"value": "启用", "label": "启用"}, {"value": "停用", "label": "停用"}]
            value = "启用" if product.status else "停用"
        fields.append(
            {
                "key": field.key,
                "label": field.label,
                "type": "single_select" if field.key == "status" else field.field_type,
                "required": field.required,
                "options": options,
            }
        )
        values[field.key] = value or ""
    anchor = db.scalar(
        select(dynamic.DataRecord).where(
            dynamic.DataRecord.table_id == PRODUCT_TABLE_ID,
            dynamic.DataRecord.source_record_id == str(product_id),
        )
    )
    raw = anchor.values if anchor else {}
    for field in catalog.extension_fields:
        key = f"extension:{field.id}"
        options = [
            {"value": o["label"], "label": o["label"]}
            for o in field.config.get("options", [])
            if o.get("active", True)
        ]
        labels = {o["id"]: o["label"] for o in field.config.get("options", [])}
        value = raw.get(str(field.id))
        if field.field_type == "single_select":
            value = labels.get(value, value)
        elif field.field_type == "multi_select" and isinstance(value, list):
            value = "；".join(labels.get(v, v) for v in value)
        fields.append(
            {
                "key": key,
                "label": field.label,
                "type": field.field_type,
                "required": field.required,
                "options": options,
            }
        )
        values[key] = "" if value is None else value
    return {
        "fields": fields,
        "values": values,
        "schema_version": catalog.table.schema_version,
        "expected_revision": product.revision,
        "extension_revision": anchor.revision if anchor else 0,
        "product": detail,
    }


def update_product(db, product_id, body, user_id):
    from .service import get_product

    config = edit_config(db, product_id)
    _product(db, product_id, body.expected_revision)
    if body.schema_version != config["schema_version"]:
        raise ImportConflict("字段配置已变化，请刷新后再编辑")
    allowed = {field["key"] for field in config["fields"]}
    if set(body.values) - allowed:
        raise ManualValidation([{"message": "包含未配置或已归档字段", "code": "UNKNOWN_FIELD"}])
    raw = {
        **{
            key: CLEAR_MARKER if value in ("", None) else value
            for key, value in body.values.items()
            if value != config["values"].get(key)
        },
        "__product_id": product_id,
        "__product_revision": body.expected_revision,
        "__extension_revision": body.extension_revision
        if body.extension_revision is not None
        else config["extension_revision"],
    }
    catalog = load_product_catalog(db)
    manifest = WorkbookManifest(
        product_schema_version=body.schema_version,
        generated_at=utc_now(),
        product_fields=catalog.product_contracts,
        price_fields=list(PRICE_FIELDS),
    )
    batch = ImportBatch(
        user_id=user_id,
        filename="页面手动编辑产品",
        file_sha256=hashlib.sha256(
            json.dumps(raw, sort_keys=True, default=str).encode()
        ).hexdigest(),
        contract_version=1,
        product_schema_version=body.schema_version,
        field_manifest=manifest.model_dump(mode="json"),
        status="uploaded",
        total_rows=1,
    )
    db.add(batch)
    db.flush()
    db.add(ImportRow(batch_id=batch.id, sheet_key="products", source_row_number=5, raw_values=raw))
    db.commit()
    preview = validate_batch(db, batch.id, user_id)
    issues = [
        i.model_dump(mode="json")
        for row in preview.rows
        for i in row.issues
        if i.severity == "block"
    ]
    if issues:
        raise ManualValidation(issues)
    commit_batch(db, batch.id, user_id)
    return get_product(db, product_id)


def _audit(db, product_id, action, before, after, user_id):
    catalog = load_product_catalog(db)
    db.add(
        dynamic.DataChange(
            table_id=PRODUCT_TABLE_ID,
            entity_type="record",
            entity_id=uuid5(PRODUCT_TABLE_ID, str(product_id)),
            action=action,
            before=before,
            after=after,
            actor_id=user_id,
            actor_role="admin",
            schema_version=catalog.table.schema_version,
            display_snapshot={},
        )
    )


def _business_blockers(db):
    # This is a preparation-only database. Do not attempt to rewrite embedded
    # order/inquiry JSON references; any business use makes deletion conservative.
    return (
        ["整理库已有订单或询价，暂不允许永久删除，请先处理业务引用"]
        if (db.scalar(select(Order.id).limit(1)) or db.scalar(select(Inquiry.id).limit(1)))
        else []
    )


def save_period(db, product_id, body, user_id, period_id=None):
    try:
        _product(db, product_id)
        before = None
        if period_id is None:
            row = price_periods.create_period_in_transaction(
                db,
                product_id,
                ProductPricePeriodCreate.model_validate(
                    body.model_dump(exclude={"expected_revision"})
                ),
                actor_id=user_id,
                source="preparation_manual",
            )
        else:
            current = db.get(master.ProductPricePeriod, period_id)
            if current is None or current.product_id != product_id:
                raise ValueError("价格区间不存在")
            if current.revision != body.expected_revision:
                raise ImportConflict("价格区间已变化，请刷新后再操作")
            before = price_periods.serialize(current)
            row = price_periods.update_period_in_transaction(
                db,
                product_id,
                period_id,
                ProductPricePeriodUpdate.model_validate(
                    body.model_dump(exclude={"expected_revision"}, exclude_unset=True)
                ),
                actor_id=user_id,
            )
        after = price_periods.serialize(row)
        _audit(db, product_id, "edit_price" if before else "create_price", before, after, user_id)
        db.commit()
        return after
    except Exception:
        db.rollback()
        raise


def delete_period(db, product_id, period_id, expected_revision, user_id):
    try:
        product = _product(db, product_id)
        row = db.get(master.ProductPricePeriod, period_id)
        if row is None or row.product_id != product_id:
            raise ValueError("价格区间不存在")
        if row.revision != expected_revision:
            raise ImportConflict("价格区间已变化，请刷新后再操作")
        blockers = _business_blockers(db)
        if blockers:
            raise ImportConflict("；".join(blockers))
        before = price_periods.serialize(row)
        db.delete(row)
        product.price_version += 1
        _audit(db, product_id, "delete_price", before, None, user_id)
        db.commit()
        return {"deleted": True}
    except Exception:
        db.rollback()
        raise


def deletion_preview(db, product_id):
    product = _product(db, product_id)
    blockers = _business_blockers(db)
    for table in Base.metadata.sorted_tables:
        if (
            table.name
            in {master.ProductPricePeriod.__tablename__, master.ProductPriceHistory.__tablename__}
            or "product_id" not in table.c
        ):
            continue
        if db.scalar(
            select(func.count()).select_from(table).where(table.c.product_id == product_id)
        ):
            blockers.append("存在关联图片、单位规则或其他业务记录，不能永久删除")
            break
    anchor = db.scalar(
        select(dynamic.DataRecord).where(
            dynamic.DataRecord.table_id == PRODUCT_TABLE_ID,
            dynamic.DataRecord.source_record_id == str(product_id),
        )
    )
    if anchor and db.scalar(
        select(dynamic.DataLink.record_id)
        .where(dynamic.DataLink.target_record_id == anchor.id)
        .limit(1)
    ):
        blockers.append("其他数据表关联了此产品，不能永久删除")
    return {
        "can_delete": not blockers,
        "reasons": blockers,
        "code": product.code,
        "expected_revision": product.revision,
        "expected_extension_revision": anchor.revision if anchor else 0,
        "period_count": db.scalar(
            select(func.count())
            .select_from(master.ProductPricePeriod)
            .where(master.ProductPricePeriod.product_id == product_id)
        )
        or 0,
    }


def delete_product(db, product_id, body, user_id):
    from .service import get_product

    try:
        lock_tables_in_transaction(db, [PRODUCT_TABLE_ID], exclusive_ids={PRODUCT_TABLE_ID})
        product = _product(db, product_id, body.expected_revision)
        preview = deletion_preview(db, product_id)
        if body.confirm_code != product.code:
            raise ImportConflict("请填写准确的产品代码确认删除")
        if not preview["can_delete"]:
            raise ImportConflict("；".join(preview["reasons"]))
        before = get_product(db, product_id)
        db.execute(
            delete(master.ProductPricePeriod).where(
                master.ProductPricePeriod.product_id == product_id
            )
        )
        anchor = db.scalar(
            select(dynamic.DataRecord)
            .where(
                dynamic.DataRecord.table_id == PRODUCT_TABLE_ID,
                dynamic.DataRecord.source_record_id == str(product_id),
            )
            .with_for_update()
        )
        if (anchor.revision if anchor else 0) != body.expected_extension_revision:
            raise ImportConflict("产品扩展资料已变化，请刷新后再删除")
        if anchor:
            anchor.status = "archived"
            anchor.revision += 1
            db.execute(
                delete(dynamic.DataUniqueValue).where(
                    dynamic.DataUniqueValue.record_id == anchor.id
                )
            )
        db.delete(product)
        _audit(db, product_id, "delete_product", before, None, user_id)
        db.commit()
        return {"deleted": True, "deleted_periods": preview["period_count"]}
    except Exception:
        db.rollback()
        raise
