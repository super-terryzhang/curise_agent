"""Prepare ID-bound page edits using the existing audited upload pipeline."""

from __future__ import annotations

from io import BytesIO
from typing import Literal

from openpyxl import Workbook
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from domains.masterdata.models import Category, Country, Port, Product, ProductPricePeriod, Supplier
from domains.masterdata.upload.models import StagingProduct
from domains.masterdata.upload.service import get_workflow_batch, parse_excel, resolve_and_score

BASIC_FIELDS = {
    "product_name_en",
    "product_name_jp",
    "code",
    "brand",
    "category_id",
    "supplier_id",
    "country_id",
    "port_id",
    "unit",
    "unit_size",
    "pack_size",
    "country_of_origin",
}
PERIOD_FIELDS = {"amount", "currency", "effective_from", "effective_to"}
FK_FIELDS = {
    "category_id": (Category, "类别"),
    "supplier_id": (Supplier, "供应商"),
    "country_id": (Country, "国家"),
    "port_id": (Port, "港口"),
}
IDENTITY_FIELDS = {"product_name_en", "code", "country_id", "port_id"}


def lock_identity_writes(db: Session) -> None:
    """Serialize identity checks with all ordinary Product INSERT/UPDATE writes.

    PostgreSQL table locks are transactional. SQLite tests are single-writer.
    Lock before product rows to keep direct batch lock ordering consistent.
    """
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("LOCK TABLE products IN SHARE ROW EXCLUSIVE MODE"))


def validate_identity_restore(
    db: Session, target: Product, patch: dict, all_patches: dict[int, dict] | None = None
) -> None:
    """Reject rollback when another product has reused the old identity."""
    rows = [
        StagingProduct(
            match_target_id=product_id, raw_data={"__direct_basic": values}, validation_errors=[]
        )
        for product_id, values in (all_patches or {target.id: patch}).items()
    ]
    _identity_errors(db, list(db.scalars(select(Product)).all()), rows)
    row = next(row for row in rows if row.match_target_id == target.id)
    if row.validation_errors:
        from domains.masterdata.errors import Conflict

        raise Conflict("原产品名称或代码已被其他产品使用，回滚会造成重复，请人工核对")


class DirectUpdateRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int = Field(gt=0)
    expected_revision: int = Field(gt=0)
    period_id: int | None = Field(default=None, gt=0)
    values: dict[str, str | int | float | None]


class DirectUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_product_ids: list[int] = Field(min_length=1, max_length=10000)
    scope: Literal["basic", "purchase", "selling"]
    operation: Literal["edit", "add"]
    rows: list[DirectUpdateRow] = Field(min_length=1, max_length=10000)


def _basic_errors(db: Session, product: Product, values: dict) -> list[str]:
    errors = []
    for field, value in values.items():
        if field in FK_FIELDS:
            model, label = FK_FIELDS[field]
            if value is not None and (type(value) is not int or db.get(model, value) is None):
                errors.append(f"{label}不存在，请重新选择")
        else:
            if value is not None and not isinstance(value, str):
                errors.append(f"{field}必须是文本")
            elif isinstance(value, str):
                max_length = Product.__table__.columns[field].type.length
                if len(value) > max_length:
                    errors.append(f"{field}长度不能超过 {max_length}")
    if "product_name_en" in values and not str(values["product_name_en"] or "").strip():
        errors.append("产品名称不能为空")
    for field, label in (("country_id", "国家"), ("port_id", "港口")):
        if values.get(field, getattr(product, field)) is None:
            errors.append(f"{label}不能为空")
    country_id = values.get("country_id", product.country_id)
    port_id = values.get("port_id", product.port_id)
    port = db.get(Port, port_id) if type(port_id) is int else None
    if port is not None and port.country_id != country_id:
        errors.append("所选港口不属于所选国家")
    return errors


def _identity_errors(db: Session, products: list[Product], rows: list[StagingProduct]) -> None:
    """Validate the prospective identities of all direct basic rows."""
    proposals = {
        row.match_target_id: row.raw_data.get("__direct_basic", {})
        for row in rows
        if row.match_target_id and row.raw_data.get("__direct_basic") is not None
    }
    seen_names: dict[tuple, int] = {}
    seen_codes: dict[tuple, int] = {}
    conflicts: set[tuple[str, tuple]] = set()
    for product in products:
        values = proposals.get(product.id, {})
        location = (
            values.get("country_id", product.country_id),
            values.get("port_id", product.port_id),
        )
        name = str(values.get("product_name_en", product.product_name_en) or "").strip().casefold()
        code = str(values.get("code", product.code) or "").strip().casefold()
        for field, key, seen in (
            ("product_name_en", location + (name,), seen_names),
            ("code", location + (code,), seen_codes),
        ):
            if not key[-1]:
                continue
            if key in seen:
                conflicts.add((field, key))
            else:
                seen[key] = product.id
    for row in rows:
        product = next((p for p in products if p.id == row.match_target_id), None)
        patch = proposals.get(row.match_target_id, {})
        if product is None:
            continue
        original_location = (product.country_id, product.port_id)
        location = (
            patch.get("country_id", product.country_id),
            patch.get("port_id", product.port_id),
        )
        invalid = False
        for field in ("product_name_en", "code"):
            original = str(getattr(product, field) or "").strip().casefold()
            proposed = str(patch.get(field, getattr(product, field)) or "").strip().casefold()
            key = location + (proposed,)
            if key != original_location + (original,) and (field, key) in conflicts:
                invalid = True
        if invalid:
            row.match_status = "error"
            row.validation_errors = [
                *(row.validation_errors or []),
                "修改后产品名称或代码在同一国家、港口重复，请调整",
            ]


def validate_direct_staging(db: Session, rows: list[StagingProduct]) -> None:
    """Repeat direct-field checks at every validation and confirmed commit."""
    for row in rows:
        patch = row.raw_data.get("__direct_basic")
        product = db.get(Product, row.match_target_id) if row.match_target_id else None
        if patch is None or product is None:
            continue
        issues = _basic_errors(db, product, patch)
        if issues:
            row.match_status = "error"
            row.validation_errors = [*(row.validation_errors or []), *issues]
    if any("__direct_basic" in row.raw_data for row in rows):
        _identity_errors(db, list(db.scalars(select(Product)).all()), rows)


def prepare_direct_update(db: Session, request: DirectUpdateRequest, *, user_id: int) -> dict:
    selected = set(request.selected_product_ids)
    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(selected))).all()}
    if set(products) != selected:
        raise ValueError("所选产品已不存在，请重新选择")
    if request.scope == "basic" and request.operation != "edit":
        raise ValueError("基本信息只能修改已有产品")
    allowed = BASIC_FIELDS if request.scope == "basic" else PERIOD_FIELDS
    workbook_rows = []
    basic_patches = []
    seen_basic: set[int] = set()
    for index, row in enumerate(request.rows, 1):
        if row.product_id not in selected:
            raise ValueError(f"第 {index} 行产品不在本次选择中")
        if not row.values or set(row.values) - allowed:
            raise ValueError(f"第 {index} 行包含不属于本次操作的字段")
        product = products[row.product_id]
        country = db.get(Country, product.country_id) if product.country_id else None
        port = db.get(Port, product.port_id) if product.port_id else None
        data = {
            "product_id": product.id,
            "expected_revision": row.expected_revision,
            "product_name": product.product_name_en,
            "product_code": product.code,
            "country": country.name if country else None,
            "port": port.name if port else None,
        }
        patch = None
        if request.scope == "basic":
            if row.period_id is not None or row.product_id in seen_basic:
                raise ValueError("基本信息每个产品只能出现一行，不应包含价格区间")
            seen_basic.add(row.product_id)
            patch = dict(row.values)
        else:
            if request.operation == "edit":
                period = db.get(ProductPricePeriod, row.period_id) if row.period_id else None
                if (
                    period is None
                    or period.product_id != product.id
                    or period.price_type != request.scope
                    or not period.status
                ):
                    raise ValueError(f"第 {index} 行价格区间不属于当前产品或已停用，请重新选择")
                values = {
                    "amount": str(period.amount),
                    "currency": period.currency,
                    "effective_from": period.effective_from.isoformat(),
                    "effective_to": period.effective_to.isoformat(),
                    **row.values,
                }
                data[f"{request.scope}_price_period_id"] = period.id
            else:
                if row.period_id is not None:
                    raise ValueError("新增区间不能携带已有区间标识")
                values = dict(row.values)
                if any(
                    values.get(field) in (None, "")
                    for field in ("amount", "effective_from", "effective_to")
                ):
                    raise ValueError(f"第 {index} 行请填写价格、开始日期和结束日期")
            # Do not allow blanks to silently keep/fall back to another price.
            if any(
                values.get(field) in (None, "")
                for field in ("amount", "effective_from", "effective_to")
            ):
                raise ValueError(f"第 {index} 行请填写完整价格区间")
            amount_key = "price" if request.scope == "purchase" else "contract_price"
            data.update(
                {
                    amount_key: values["amount"],
                    "currency": values.get("currency"),
                    f"{request.scope}_price_effective_from": values["effective_from"],
                    f"{request.scope}_price_effective_to": values["effective_to"],
                }
            )
        workbook_rows.append(data)
        basic_patches.append(patch)
    headers = list(dict.fromkeys(key for row in workbook_rows for key in row))
    wb = Workbook()
    ws = wb.active
    ws.title = "页面编辑"
    ws.append(headers)
    for row in workbook_rows:
        ws.append([row.get(key) for key in headers])
    blob = BytesIO()
    wb.save(blob)
    batch = parse_excel(
        db,
        file_bytes=blob.getvalue(),
        filename="页面批量更新.xlsx",
        user_id=user_id,
        strict_headers=False,
        workflow_version=3,
    )
    staging = list(
        db.scalars(
            select(StagingProduct)
            .where(StagingProduct.batch_id == batch.id)
            .order_by(StagingProduct.row_index)
        ).all()
    )
    for row, patch in zip(staging, basic_patches, strict=True):
        if patch is not None:
            row.raw_data = {**row.raw_data, "__direct_basic": patch}
    db.commit()
    resolve_and_score(db, batch_id=batch.id, user_id=user_id)
    # Page indices refer to editable rows, not the internal workbook header.
    for index, row in enumerate(staging, 1):
        row.source_row_number = index
    db.commit()
    return get_workflow_batch(db, batch_id=batch.id, user_id=user_id)
