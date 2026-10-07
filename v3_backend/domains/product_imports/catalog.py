"""Resolve one immutable product-import catalog from the current table structure."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from domains.dynamic_data.errors import Conflict
from domains.dynamic_data.models import DataField, DataTable
from scripts.seed_clean_product_fields import PRODUCT_TABLE_ID

from .contracts import WorkbookField


@dataclass(frozen=True)
class CoreField:
    key: str
    label: str
    field_type: str
    attribute: str
    required: bool = False
    relation: str | None = None

    def contract(self) -> WorkbookField:
        return WorkbookField(
            key=self.key,
            label=self.label,
            field_type=self.field_type,
            required=self.required,
        )


PRODUCT_CORE_PREFIX = (
    CoreField("product_code", "产品代码", "text", "code", True),
    CoreField("port", "港口", "reference", "port_id", True, "port"),
    CoreField("product_name", "产品名称", "text", "product_name_en", True),
    CoreField("supplier", "供应商", "reference", "supplier_id", False, "supplier"),
    CoreField("unit", "单位", "text", "unit"),
    CoreField("category", "商品分类", "reference", "category_id", False, "category"),
    CoreField("brand", "品牌", "text", "brand"),
)
PRODUCT_CORE_SUFFIX = (
    CoreField("status", "状态", "boolean", "status", True),
    CoreField("country", "国家", "reference", "country_id", True, "country"),
)
PRICE_FIELDS = (
    WorkbookField(key="product_code", label="产品代码", field_type="text", required=True),
    WorkbookField(key="port", label="港口", field_type="reference", required=True),
    WorkbookField(key="price_type", label="价格类型", field_type="single_select", required=True),
    WorkbookField(key="amount", label="价格", field_type="number", required=True),
    WorkbookField(key="currency", label="币种", field_type="currency", required=False),
    WorkbookField(key="effective_from", label="开始日期", field_type="date", required=True),
    WorkbookField(key="effective_to", label="结束日期", field_type="date", required=True),
)


@dataclass(frozen=True)
class ProductCatalog:
    table: DataTable
    core_prefix: tuple[CoreField, ...]
    extension_fields: tuple[DataField, ...]
    core_suffix: tuple[CoreField, ...]

    @property
    def product_contracts(self) -> list[WorkbookField]:
        extensions = [
            WorkbookField(
                key=f"extension:{field.id}",
                label=field.label,
                field_type=field.field_type,
                required=field.required,
                option_ids=[
                    str(option["id"]) for option in field.config.get("options", [])
                ],
            )
            for field in self.extension_fields
        ]
        return [
            *(field.contract() for field in self.core_prefix),
            *extensions,
            *(field.contract() for field in self.core_suffix),
        ]


def load_product_catalog(db: Session) -> ProductCatalog:
    table = db.get(DataTable, PRODUCT_TABLE_ID)
    if (
        table is None
        or table.status != "active"
        or table.table_kind != "system"
        or table.system_key != "products"
    ):
        raise Conflict("PRODUCT_TABLE_MISSING", "产品系统数据表不存在或未启用")
    extensions = tuple(
        db.scalars(
            select(DataField)
            .where(DataField.table_id == table.id, DataField.status == "active")
            .order_by(DataField.sort_order, DataField.id)
        )
    )
    labels = [field.label.strip().casefold() for field in extensions]
    if len(labels) != len(set(labels)):
        raise Conflict("DUPLICATE_FIELD_LABEL", "产品启用字段存在重名，不能生成唯一表头")
    return ProductCatalog(
        table=table,
        core_prefix=PRODUCT_CORE_PREFIX,
        extension_fields=extensions,
        core_suffix=PRODUCT_CORE_SUFFIX,
    )

