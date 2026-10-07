"""The clean database gets one stable, configurable product classification field."""

from copy import deepcopy

import pytest
from sqlalchemy import func, select

from domains.dynamic_data.errors import Conflict
from domains.dynamic_data.models import DataField, DataTable
from scripts.seed_clean_product_fields import (
    BUSINESS_CLASSIFICATION_FIELD_ID,
    BUSINESS_CLASSIFICATION_OPTIONS,
    PRODUCT_TABLE_ID,
    seed_product_business_classification,
)


def add_product_table(db):
    table = DataTable(
        id=PRODUCT_TABLE_ID,
        name="产品",
        table_kind="system",
        system_key="products",
        status="active",
        schema_version=1,
        created_by=0,
        updated_by=0,
    )
    db.add(table)
    db.commit()
    return table


def test_seed_is_exact_and_second_run_does_not_mutate(db):
    table = add_product_table(db)

    first = seed_product_business_classification(db, actor_id=0)
    db.commit()
    version = db.get(DataTable, table.id).schema_version
    second = seed_product_business_classification(db, actor_id=99)
    db.commit()

    assert first.id == second.id == BUSINESS_CLASSIFICATION_FIELD_ID
    assert version == db.get(DataTable, table.id).schema_version == 2
    assert db.scalar(select(func.count()).select_from(DataField)) == 1
    assert first.label == "业务分类"
    assert first.field_type == "single_select"
    assert first.default_value == str(BUSINESS_CLASSIFICATION_OPTIONS[-1][0])
    assert first.config == {
        "options": [
            {"id": str(option_id), "label": label, "active": True}
            for option_id, label in BUSINESS_CLASSIFICATION_OPTIONS
        ]
    }


def test_seed_rejects_mismatched_fixed_field_instead_of_overwriting(db):
    add_product_table(db)
    expected = seed_product_business_classification(db, actor_id=0)
    db.commit()
    expected.config = deepcopy(expected.config)
    expected.config["options"][0]["label"] = "错误名称"
    db.commit()

    with pytest.raises(Conflict, match="固定配置"):
        seed_product_business_classification(db, actor_id=0)

    assert db.get(DataField, BUSINESS_CLASSIFICATION_FIELD_ID).config["options"][0][
        "label"
    ] == "错误名称"


def test_seed_rejects_another_active_field_with_same_normalized_label(db):
    table = add_product_table(db)
    db.add(
        DataField(
            table_id=table.id,
            label=" 业务分类 ",
            field_type="text",
            sort_order=0,
            status="active",
            schema_version=1,
        )
    )
    db.commit()

    with pytest.raises(Conflict, match="字段名称"):
        seed_product_business_classification(db, actor_id=0)
