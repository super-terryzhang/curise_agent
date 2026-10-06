"""Read-only adapters exposing selected core tables through the data-table API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from domains.masterdata import Category, Country, Port, Product, Supplier
from domains.orders import Order

from .errors import Forbidden, NotFound, ValidationError
from .models import DataRecord, DataTable
from .permissions import ADMINS, require_writer
from .schemas import Actor, FieldResponse, Page, RecordQuery, RecordResponse


@dataclass(frozen=True)
class SystemField:
    key: str
    label: str
    field_type: str
    attribute: str
    required: bool = False
    relation: str | None = None

    @property
    def id(self) -> UUID:
        return uuid5(
            NAMESPACE_URL,
            f"https://cruise-agent.local/data-tables/fields/{self.key}",
        )


@dataclass(frozen=True)
class SystemTableAdapter:
    key: str
    model: type
    fields: tuple[SystemField, ...]
    search_attributes: tuple[str, ...]
    display_attribute: str
    business_url: str


PRODUCT_FIELDS = (
    SystemField("products.product_name_en", "英文名称", "text", "product_name_en", True),
    SystemField("products.product_name_jp", "日文名称", "text", "product_name_jp"),
    SystemField("products.code", "产品代码", "text", "code"),
    SystemField("products.supplier", "供应商", "text", "supplier_id", relation="supplier"),
    SystemField("products.country", "国家", "text", "country_id", relation="country"),
    SystemField("products.category", "分类", "text", "category_id", relation="category"),
    SystemField("products.port", "港口", "text", "port_id", relation="port"),
    SystemField("products.unit", "单位", "text", "unit"),
    SystemField("products.brand", "品牌", "text", "brand"),
    SystemField("products.status", "状态", "boolean", "status"),
)

SUPPLIER_FIELDS = (
    SystemField("suppliers.name", "供应商名称", "text", "name", True),
    SystemField("suppliers.country", "国家", "text", "country_id", relation="country"),
    SystemField("suppliers.contact", "联系人", "text", "contact"),
    SystemField("suppliers.email", "邮箱", "text", "email"),
    SystemField("suppliers.phone", "电话", "text", "phone"),
    SystemField("suppliers.address", "地址", "text", "address"),
    SystemField(
        "suppliers.default_payment_method", "默认付款方式", "text", "default_payment_method"
    ),
    SystemField("suppliers.default_payment_terms", "默认付款条件", "text", "default_payment_terms"),
    SystemField("suppliers.status", "状态", "boolean", "status"),
)

ORDER_FIELDS = (
    SystemField("orders.po_number", "PO 编号", "text", "po_number"),
    SystemField("orders.ship_name", "船名", "text", "ship_name"),
    SystemField("orders.loading_date", "装船日期", "date", "loading_date"),
    SystemField(
        "orders.destination_port",
        "目标港口",
        "text",
        "destination_port",
        relation="destination_port",
    ),
    SystemField("orders.status", "处理状态", "text", "status"),
    SystemField("orders.created_at", "进入系统时间", "datetime", "created_at"),
)

SYSTEM_TABLES = {
    "products": SystemTableAdapter(
        key="products",
        model=Product,
        fields=PRODUCT_FIELDS,
        search_attributes=("product_name_en", "product_name_jp", "code", "brand"),
        display_attribute="product_name_en",
        business_url="/dashboard/data/products/{id}",
    ),
    "suppliers": SystemTableAdapter(
        key="suppliers",
        model=Supplier,
        fields=SUPPLIER_FIELDS,
        search_attributes=("name", "contact", "email", "phone"),
        display_attribute="name",
        business_url="/dashboard/data?tab=suppliers&edit={id}",
    ),
    "orders": SystemTableAdapter(
        key="orders",
        model=Order,
        fields=ORDER_FIELDS,
        search_attributes=("po_number", "ship_name", "filename", "destination_port"),
        display_attribute="po_number",
        business_url="/dashboard/orders/{id}",
    ),
}


def _adapter(table: DataTable) -> SystemTableAdapter:
    adapter = SYSTEM_TABLES.get(table.system_key or "")
    if table.table_kind != "system" or adapter is None:
        raise ValidationError("INVALID_SYSTEM_TABLE", "系统数据表配置无效")
    return adapter


def system_fields(table: DataTable) -> list[FieldResponse]:
    adapter = _adapter(table)
    return [
        FieldResponse(
            id=field.id,
            table_id=table.id,
            label=field.label,
            field_type=field.field_type,
            required=field.required,
            unique=False,
            default_value=None,
            config={},
            target_table_id=None,
            source="core",
            locked=True,
            system_key=field.key.split(".", 1)[1],
            status="active",
            sort_order=index,
            schema_version=table.schema_version,
            created_at=table.created_at,
            updated_at=table.updated_at,
        )
        for index, field in enumerate(adapter.fields)
    ]


def core_field_count(table: DataTable) -> int:
    return len(_adapter(table).fields)


def _require_actor(actor: Actor | None) -> Actor:
    if actor is None:
        raise Forbidden("RECORD_FORBIDDEN", "查看系统数据表需要登录")
    require_writer(actor)
    return actor


def _visible_statement(adapter: SystemTableAdapter, actor: Actor):
    statement = select(adapter.model)
    if adapter.key == "orders" and actor.role not in ADMINS:
        statement = statement.where(Order.user_id == actor.id)
    return statement


def _with_search(statement, adapter: SystemTableAdapter, q: str | None):
    needle = (q or "").strip()
    if not needle:
        return statement
    conditions = [
        func.lower(func.coalesce(getattr(adapter.model, name), "")).contains(
            needle.lower(), autoescape=True
        )
        for name in adapter.search_attributes
    ]
    return statement.where(or_(*conditions))


def count_system_records(
    db: Session, table: DataTable, *, actor: Actor | None, q: str | None = None
) -> int:
    checked_actor = _require_actor(actor)
    adapter = _adapter(table)
    statement = _with_search(_visible_statement(adapter, checked_actor), adapter, q)
    return int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)


def _parse_source_id(source_record_id: str | int) -> int:
    raw = str(source_record_id)
    if not raw.isascii() or not raw.isdigit() or raw.startswith("0"):
        raise NotFound("RECORD_NOT_FOUND", "记录不存在于此数据表")
    value = int(raw)
    if value <= 0 or str(value) != raw:
        raise NotFound("RECORD_NOT_FOUND", "记录不存在于此数据表")
    return value


def require_source_row(
    db: Session,
    table: DataTable,
    source_record_id: str | int,
    *,
    actor: Actor | None,
    for_update: bool = False,
):
    """Load one Actor-visible source row, optionally locking it for extension writes."""
    checked_actor = _require_actor(actor)
    adapter = _adapter(table)
    source_id = _parse_source_id(source_record_id)
    statement = _visible_statement(adapter, checked_actor).where(adapter.model.id == source_id)
    if for_update:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        raise NotFound("RECORD_NOT_FOUND", "记录不存在于此数据表")
    return row


def visible_source_id_statement(table: DataTable, *, actor: Actor | None):
    """Return an actor-scoped source-ID query for joins and bounded pagination."""
    checked_actor = _require_actor(actor)
    adapter = _adapter(table)
    return _visible_statement(adapter, checked_actor).with_only_columns(
        cast(adapter.model.id, String).label("source_record_id")
    )


def _relation_labels(
    db: Session, adapter: SystemTableAdapter, rows: list[Any]
) -> dict[str, dict[int, str]]:
    labels: dict[str, dict[int, str]] = {}
    relations = {
        field.relation: field.attribute
        for field in adapter.fields
        if field.relation and field.relation != "destination_port"
    }
    models = {
        "supplier": Supplier,
        "country": Country,
        "category": Category,
        "port": Port,
    }
    for relation, attribute in relations.items():
        ids = {getattr(row, attribute) for row in rows if getattr(row, attribute) is not None}
        labels[relation] = (
            dict(
                db.execute(
                    select(models[relation].id, models[relation].name).where(
                        models[relation].id.in_(ids)
                    )
                ).all()
            )
            if ids
            else {}
        )
    if adapter.key == "orders":
        ids = {row.port_id for row in rows if row.port_id is not None}
        labels["destination_port"] = (
            dict(db.execute(select(Port.id, Port.name).where(Port.id.in_(ids))).all())
            if ids
            else {}
        )
    return labels


def _anchors(db: Session, table_id: UUID, rows: list[Any]) -> dict[str, DataRecord]:
    ids = [str(row.id) for row in rows]
    if not ids:
        return {}
    return {
        record.source_record_id: record
        for record in db.scalars(
            select(DataRecord).where(
                DataRecord.table_id == table_id,
                DataRecord.source_record_id.in_(ids),
                DataRecord.status == "active",
            )
        )
        if record.source_record_id is not None
    }


def _field_value(field: SystemField, row: Any, labels: dict[str, dict[int, str]]) -> Any:
    if field.relation == "destination_port":
        return labels.get("destination_port", {}).get(row.port_id) or row.destination_port
    if field.relation:
        return labels.get(field.relation, {}).get(getattr(row, field.attribute))
    value = getattr(row, field.attribute)
    return value


def _record_response(
    table: DataTable,
    adapter: SystemTableAdapter,
    row: Any,
    labels: dict[str, dict[int, str]],
    anchor: DataRecord | None,
) -> RecordResponse:
    values = {str(field.id): _field_value(field, row, labels) for field in adapter.fields}
    if anchor is not None:
        values.update(anchor.values)
    source_created = getattr(row, "created_at", None)
    source_updated = getattr(row, "updated_at", None)
    return RecordResponse(
        id=str(row.id),
        table_id=table.id,
        values=values,
        revision=anchor.revision if anchor else 0,
        schema_version=table.schema_version,
        status="active",
        created_at=(anchor.created_at if anchor else source_created) or table.created_at,
        updated_at=(anchor.updated_at if anchor else source_updated) or table.updated_at,
        created_by=anchor.created_by if anchor else None,
        updated_by=anchor.updated_by if anchor else None,
        display_label=str(getattr(row, adapter.display_attribute) or row.id),
        business_url=adapter.business_url.format(id=row.id),
    )


def list_system_records(
    db: Session, table: DataTable, query: RecordQuery, *, actor: Actor | None
) -> Page[RecordResponse]:
    checked_actor = _require_actor(actor)
    adapter = _adapter(table)
    if query.status != "active" or query.filters or query.sort_field_id:
        raise ValidationError("UNSUPPORTED_SYSTEM_QUERY", "系统数据表当前只支持关键词搜索和分页")
    statement = _with_search(_visible_statement(adapter, checked_actor), adapter, query.q)
    total = int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    rows = list(
        db.scalars(
            statement.order_by(adapter.model.id)
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
    )
    labels = _relation_labels(db, adapter, rows)
    anchors = _anchors(db, table.id, rows)
    return Page(
        items=[
            _record_response(table, adapter, row, labels, anchors.get(str(row.id))) for row in rows
        ],
        total=total,
        page=query.page,
        page_size=query.page_size,
    )


def get_system_record(
    db: Session,
    table: DataTable,
    source_record_id: str | int,
    *,
    actor: Actor | None,
) -> RecordResponse:
    adapter = _adapter(table)
    row = require_source_row(db, table, source_record_id, actor=actor)
    labels = _relation_labels(db, adapter, [row])
    anchor = _anchors(db, table.id, [row]).get(str(row.id))
    return _record_response(table, adapter, row, labels, anchor)
