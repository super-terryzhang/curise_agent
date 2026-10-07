"""Install the fixed product fields required by the clean-database import flow."""

from __future__ import annotations

import argparse
import os
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.orm import Session

from domains.dynamic_data.errors import Conflict
from domains.dynamic_data.models import DataField, DataTable, utc_now
from domains.dynamic_data.schemas import FieldCreate
from domains.dynamic_data.structures import ensure_unique_active_field_label
from domains.dynamic_data.validation import validate_field_definition
from scripts.bootstrap_fresh_database import validate_target

PRODUCT_TABLE_ID = UUID("025588dd-ae63-5607-9e78-1179a500ed6e")
BUSINESS_CLASSIFICATION_FIELD_ID = UUID("cc022b56-84dc-5fa1-aa37-caa607ebaa38")
BUSINESS_CLASSIFICATION_OPTIONS = (
    (UUID("30293f3e-d76a-5a50-9292-d0fc8e2f15c9"), "中标产品"),
    (UUID("51493dd7-3b7f-5c60-98f0-c88f03c96080"), "未中标产品"),
    (UUID("9ed864e6-c49a-5eec-adae-5b7b21d1415c"), "临时产品"),
    (UUID("9ba6cb77-7f2f-581a-8f17-a5ca75fe9524"), "未分类"),
)


def expected_definition():
    return validate_field_definition(
        FieldCreate(
            id=BUSINESS_CLASSIFICATION_FIELD_ID,
            label="业务分类",
            field_type="single_select",
            required=False,
            unique=False,
            default_value=str(BUSINESS_CLASSIFICATION_OPTIONS[-1][0]),
            config={
                "options": [
                    {"id": str(option_id), "label": label, "active": True}
                    for option_id, label in BUSINESS_CLASSIFICATION_OPTIONS
                ]
            },
            expected_schema_version=1,
        )
    )


def _matches_expected(field: DataField) -> bool:
    expected = expected_definition()
    return all(
        (
            field.table_id == PRODUCT_TABLE_ID,
            field.label == expected.label,
            field.field_type == expected.field_type,
            field.required == expected.required,
            field.unique == expected.unique,
            field.default_value == expected.default_value,
            field.config == expected.config,
            field.target_table_id == expected.target_table_id,
            field.sort_order == 0,
            field.status == "active",
            field.schema_version == 2,
        )
    )


def seed_product_business_classification(db: Session, actor_id: int) -> DataField:
    """Create the fixed field once; reject drift instead of silently rewriting it."""

    table = db.scalar(
        select(DataTable).where(DataTable.id == PRODUCT_TABLE_ID).with_for_update()
    )
    if table is None or table.table_kind != "system" or table.system_key != "products":
        raise Conflict("PRODUCT_TABLE_MISSING", "产品系统数据表不存在或固定编号不匹配")

    existing = db.get(DataField, BUSINESS_CLASSIFICATION_FIELD_ID)
    if existing is not None:
        if not _matches_expected(existing):
            raise Conflict(
                "BUSINESS_CLASSIFICATION_DRIFT",
                "业务分类固定配置与预期不一致；不会自动覆盖，请先人工核对固定配置",
            )
        return existing

    definition = expected_definition()
    ensure_unique_active_field_label(db, table.id, definition.label)
    last_sort_order = db.scalar(
        select(sa.func.max(DataField.sort_order)).where(DataField.table_id == table.id)
    )
    sort_order = (last_sort_order if last_sort_order is not None else -1) + 1
    table.schema_version += 1
    table.updated_at = utc_now()
    table.updated_by = actor_id
    field = DataField(
        **definition.model_dump(),
        table_id=table.id,
        sort_order=sort_order,
        status="active",
        schema_version=table.schema_version,
    )
    db.add(field)
    db.flush()
    return field


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--database-url-env", default="FRESH_DATABASE_URL")
    parser.add_argument("--actor-id", type=int, default=0)
    args = parser.parse_args()
    raw_url = os.environ.get(args.database_url_env, "")
    if not raw_url:
        raise SystemExit(f"Missing environment variable {args.database_url_env}")
    engine = sa.create_engine(raw_url, pool_pre_ping=True)
    validate_target(engine.url, expected_database=args.expected_database)
    try:
        with Session(engine) as db, db.begin():
            field = seed_product_business_classification(db, args.actor_id)
            field_id = field.id
    finally:
        engine.dispose()
    print(f"Product business classification ready: field_id={field_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
