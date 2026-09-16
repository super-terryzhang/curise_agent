"""Settings business logic — admin CRUD for templates, schemas, locations, config.

Keeps each entity's CRUD short and consistent with v2 semantics. Template
analysis (the LLM-heavy part) lives in `_analyze.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from domains.settings import repository as repo
from domains.settings._analyze import (
    analyze_excel_template,
    analyze_pdf_template,
    infer_template_metadata,
)
from domains.settings.errors import BadRequest, Conflict, NotFound, SettingsError
from domains.settings.models import (
    CompanyConfig,
    DeliveryLocation,
    FieldDefinition,
    FieldSchema,
    OrderFormatTemplate,
)
from domains.settings.schemas import (
    CompanyConfigUpdate,
    DeliveryLocationCreate,
    DeliveryLocationUpdate,
    FieldDefinitionCreate,
    FieldDefinitionUpdate,
    FieldSchemaCreate,
    OrderFormatTemplateCreate,
    OrderFormatTemplateUpdate,
    TemplateInferRequest,
)

__all__ = [
    "SettingsError",
    "NotFound",
    "Conflict",
    "BadRequest",
    # field schemas
    "list_field_schemas",
    "create_field_schema",
    "get_field_schema",
    "update_field_schema",
    "delete_field_schema",
    "seed_default_field_schema",
    # field definitions
    "add_field_definition",
    "update_field_definition",
    "delete_field_definition",
    # order templates
    "list_order_templates",
    "create_order_template",
    "get_order_template",
    "update_order_template",
    "delete_order_template",
    "infer_order_template_meta",
    "analyze_order_template_pdf",
    # delivery locations
    "list_delivery_locations",
    "create_delivery_location",
    "update_delivery_location",
    "delete_delivery_location",
    # company config
    "list_company_config",
    "update_company_config",
    # template analysis (re-exported)
    "analyze_excel_template",
]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ═════ Field Schema CRUD ═══════════════════════════════════════


def list_field_schemas(db: Session) -> list[FieldSchema]:
    return repo.list_field_schemas(db)


def create_field_schema(db: Session, body: FieldSchemaCreate, *, created_by: int) -> FieldSchema:
    schema = FieldSchema(
        name=body.name,
        description=body.description,
        created_by=created_by,
    )
    db.add(schema)
    db.commit()
    db.refresh(schema)
    return schema


def get_field_schema(db: Session, schema_id: int) -> FieldSchema:
    schema = repo.get_field_schema(db, schema_id)
    if schema is None:
        raise NotFound("字段模式不存在")
    return schema


def update_field_schema(db: Session, schema_id: int, body: FieldSchemaCreate) -> FieldSchema:
    schema = get_field_schema(db, schema_id)
    schema.name = body.name
    if body.description is not None:
        schema.description = body.description
    db.commit()
    db.refresh(schema)
    return schema


def delete_field_schema(db: Session, schema_id: int) -> None:
    schema = get_field_schema(db, schema_id)
    db.delete(schema)
    db.commit()


# ─── Field Definitions ─────────────────────────────────────


def add_field_definition(
    db: Session, schema_id: int, body: FieldDefinitionCreate
) -> FieldDefinition:
    get_field_schema(db, schema_id)  # validate parent exists
    defn = FieldDefinition(schema_id=schema_id, **body.model_dump())
    db.add(defn)
    db.commit()
    db.refresh(defn)
    return defn


def update_field_definition(
    db: Session, schema_id: int, def_id: int, body: FieldDefinitionUpdate
) -> FieldDefinition:
    defn = repo.get_field_definition(db, schema_id, def_id)
    if defn is None:
        raise NotFound("字段定义不存在")
    for key, val in body.model_dump(exclude_unset=True).items():
        setattr(defn, key, val)
    db.commit()
    db.refresh(defn)
    return defn


def delete_field_definition(db: Session, schema_id: int, def_id: int) -> None:
    defn = repo.get_field_definition(db, schema_id, def_id)
    if defn is None:
        raise NotFound("字段定义不存在")
    if defn.is_core:
        raise BadRequest("核心字段不可删除")
    db.delete(defn)
    db.commit()


# ─── Default Field Schema seed ─────────────────────────────


_CORE_FIELDS: list[dict[str, Any]] = [
    {
        "field_key": "product_name",
        "field_label": "品名",
        "field_type": "string",
        "is_core": True,
        "is_required": True,
        "sort_order": 1,
        "extraction_hint": "产品名称/品名/商品名",
    },
    {
        "field_key": "product_code",
        "field_label": "商品代码",
        "field_type": "string",
        "is_core": True,
        "sort_order": 2,
        "extraction_hint": "商品コード/Item Code",
    },
    {
        "field_key": "quantity",
        "field_label": "数量",
        "field_type": "number",
        "is_core": True,
        "is_required": True,
        "sort_order": 3,
        "extraction_hint": "数量/Qty/Quantity",
    },
    {
        "field_key": "unit",
        "field_label": "单位",
        "field_type": "string",
        "is_core": True,
        "sort_order": 4,
        "extraction_hint": "单位/Unit (CT/KG/L/PCS)",
    },
    {
        "field_key": "unit_price",
        "field_label": "单价",
        "field_type": "number",
        "is_core": True,
        "sort_order": 5,
        "extraction_hint": "单价/Unit Price",
    },
    {
        "field_key": "currency",
        "field_label": "币种",
        "field_type": "string",
        "is_core": True,
        "sort_order": 6,
        "extraction_hint": "币种/Currency (USD/JPY/AUD)",
    },
    {
        "field_key": "delivery_date",
        "field_label": "交货日期",
        "field_type": "date",
        "is_core": True,
        "sort_order": 7,
        "extraction_hint": "纳品日/Delivery Date",
    },
    {
        "field_key": "po_number",
        "field_label": "PO番号",
        "field_type": "string",
        "is_core": True,
        "sort_order": 8,
        "extraction_hint": "PO No/注文番号",
    },
]


def seed_default_field_schema(db: Session, *, created_by: int) -> FieldSchema:
    existing = repo.get_default_field_schema(db)
    if existing is not None:
        return existing
    schema = FieldSchema(
        name="默认字段模式",
        description="系统默认的 8 个核心字段",
        is_default=True,
        created_by=created_by,
    )
    db.add(schema)
    db.flush()
    for field_data in _CORE_FIELDS:
        db.add(FieldDefinition(schema_id=schema.id, **field_data))
    db.commit()
    db.refresh(schema)
    return schema


# ═════ Order Format Template CRUD ═══════════════════════════════


def list_order_templates(db: Session) -> list[OrderFormatTemplate]:
    return repo.list_order_templates(db)


def create_order_template(
    db: Session, body: OrderFormatTemplateCreate, *, created_by: int
) -> OrderFormatTemplate:
    tpl = OrderFormatTemplate(**body.model_dump(), created_by=created_by)
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


def get_order_template(db: Session, tpl_id: int) -> OrderFormatTemplate:
    tpl = repo.get_order_template(db, tpl_id)
    if tpl is None:
        raise NotFound("订单格式模板不存在")
    return tpl


def update_order_template(
    db: Session, tpl_id: int, body: OrderFormatTemplateUpdate
) -> OrderFormatTemplate:
    tpl = get_order_template(db, tpl_id)
    for key, val in body.model_dump(exclude_unset=True).items():
        setattr(tpl, key, val)
    db.commit()
    db.refresh(tpl)
    return tpl


def delete_order_template(db: Session, tpl_id: int) -> None:
    tpl = get_order_template(db, tpl_id)
    db.delete(tpl)
    db.commit()


def infer_order_template_meta(body: TemplateInferRequest) -> dict[str, Any]:
    return infer_template_metadata(
        raw_text=body.raw_text, headers=body.headers, file_type=body.file_type
    )


def analyze_order_template_pdf(content: bytes) -> dict[str, Any]:
    return analyze_pdf_template(content)


# ═════ Delivery Locations ═══════════════════════════════════════


def list_delivery_locations(db: Session, *, port_id: int | None = None) -> list[DeliveryLocation]:
    return repo.list_delivery_locations(db, port_id=port_id)


def create_delivery_location(
    db: Session, body: DeliveryLocationCreate, *, created_by: int
) -> DeliveryLocation:
    loc = DeliveryLocation(**body.model_dump(), created_by=created_by)
    db.add(loc)
    db.commit()
    db.refresh(loc)
    return loc


def update_delivery_location(
    db: Session, loc_id: int, body: DeliveryLocationUpdate
) -> DeliveryLocation:
    loc = repo.get_delivery_location(db, loc_id)
    if loc is None:
        raise NotFound("配送点不存在")
    for key, val in body.model_dump(exclude_unset=True).items():
        setattr(loc, key, val)
    db.commit()
    db.refresh(loc)
    return loc


def delete_delivery_location(db: Session, loc_id: int) -> None:
    loc = repo.get_delivery_location(db, loc_id)
    if loc is None:
        raise NotFound("配送点不存在")
    db.delete(loc)
    db.commit()


# ═════ Company Config ═══════════════════════════════════════════


def list_company_config(db: Session) -> list[CompanyConfig]:
    return repo.list_company_config(db)


def update_company_config(db: Session, body: CompanyConfigUpdate, *, updated_by: int) -> None:
    for item in body.items:
        row = repo.get_company_config_item(db, item.key)
        if row is None:
            db.add(
                CompanyConfig(
                    key=item.key,
                    value=item.value,
                    label=item.label,
                    updated_by=updated_by,
                )
            )
        else:
            row.value = item.value
            if item.label is not None:
                row.label = item.label
            row.updated_by = updated_by
    _ = _now()  # ensure UTC import not flagged as unused in mypy strict
    db.commit()
