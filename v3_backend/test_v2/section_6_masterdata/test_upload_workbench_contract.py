"""Deterministic workbench product-upload contract tests."""

from __future__ import annotations

from domains.masterdata.upload import (
    commit_validated_batch,
    get_workflow_batch,
    get_workflow_rows,
    parse_excel,
    validate_workflow_batch,
)
from domains.masterdata.upload.errors import BatchValidationFailed
from test_v2.fixtures.helpers import make_excel, seed_product


def _upload(db, rows: list[dict]):
    return parse_excel(
        db,
        file_bytes=make_excel(rows),
        filename="workbench.xlsx",
        user_id=1,
        strict_headers=False,
        workflow_version=2,
    )


def test_validation_blocks_unknown_headers_and_commit(db):
    batch = _upload(
        db,
        [{"product_name": "Apple", "typo_price": 10}],
    )

    snapshot = validate_workflow_batch(db, batch_id=batch.id, user_id=1)

    assert snapshot["status"] == "resolved"
    assert snapshot["can_continue"] is False
    assert snapshot["header_diagnostics"]["blocking_issues"][0]["code"] == "unknown_header"
    try:
        commit_validated_batch(db, batch_id=batch.id, user_id=1)
    except BatchValidationFailed:
        pass
    else:  # pragma: no cover - explicit contract failure
        raise AssertionError("invalid batch was committed")


def test_workflow_rows_are_unified_paginated_and_identified(db):
    seed_product(db, code="A1", name="Apple", price=10.0)
    batch = _upload(
        db,
        [
            {"product_code": "A1", "product_name": "Apple", "price": 12},
            {"product_name": "New", "price": 3, "currency": "JPY"},
        ],
    )
    validate_workflow_batch(db, batch_id=batch.id, user_id=1)

    page = get_workflow_rows(db, batch_id=batch.id, user_id=1, view="changes", page=1, page_size=1)

    assert page["page"] == 1
    assert page["page_size"] == 1
    assert page["total_items"] == 2
    assert page["total_pages"] == 2
    row = page["items"][0]
    assert row["kind"] in {"create", "update"}
    assert row["source_row_number"] == 2
    assert row["identity"]["product_name"] == "Apple"
    assert row["fields"]
    assert {"key", "label", "before", "after", "changed"} <= set(row["fields"][0])
    if row["kind"] == "create":
        price = next(field for field in row["fields"] if field["key"] == "price")
        assert price["currency"] == "JPY"


def test_valid_batch_can_continue_and_commit_once(db):
    batch = _upload(
        db,
        [{"product_name": "New", "price": 3}],
    )
    snapshot = validate_workflow_batch(db, batch_id=batch.id, user_id=1)
    assert snapshot["can_continue"] is True
    assert snapshot["summary"] == {"create": 1, "update": 0, "skip": 0, "error": 0, "total": 1}

    result = commit_validated_batch(db, batch_id=batch.id, user_id=1)
    assert result["created"] == 1
    assert get_workflow_batch(db, batch_id=batch.id, user_id=1)["status"] == "completed"


def test_workflow_row_validation_messages_are_consistently_chinese(db):
    batch = _upload(
        db,
        [
            {"product_name": "Price text", "price": "twelve dollars"},
            {"product_name": "Negative selling", "contract_price": -5},
            {
                "product_name": "Purchase dates reversed",
                "purchase_price_effective_from": "2027-12-31",
                "purchase_price_effective_to": "2027-01-01",
            },
            {
                "product_name": "Selling date invalid",
                "selling_price_effective_from": "2026-13-40",
                "selling_price_effective_to": "2027-12-31",
            },
        ],
    )
    validate_workflow_batch(db, batch_id=batch.id, user_id=1)

    page = get_workflow_rows(
        db, batch_id=batch.id, user_id=1, view="issues", page=1, page_size=20
    )

    messages = [item["issues"][0]["message"] for item in page["items"]]
    assert messages == [
        "采购价必须是数字，不能是公式或其他文本",
        "卖价必须在 0 至 99999999.99 之间",
        "采购价有效开始日期不能晚于结束日期",
        "卖价开始日期“2026-13-40”不是有效日期，请使用 YYYY-MM-DD 格式（例如：2026-05-30）",
    ]
    assert all("price" not in message and "contract_price" not in message for message in messages)


def test_workflow_required_and_masterdata_messages_are_chinese(db):
    batch = _upload(
        db,
        [
            {"product_name": "Missing country", "country": "", "port": "TestPort"},
            {"product_name": "Missing port", "country": "TestCountry", "port": ""},
            {"product_name": "Unknown supplier", "supplier": "Unknown Supplier"},
        ],
    )
    validate_workflow_batch(db, batch_id=batch.id, user_id=1)

    page = get_workflow_rows(
        db, batch_id=batch.id, user_id=1, view="issues", page=1, page_size=20
    )
    messages = [item["issues"][0]["message"] for item in page["items"]]

    assert messages == [
        "国家为必填项，请填写国家名称（例如：Japan）",
        "港口为必填项，请填写港口名称（例如：Tokyo）",
        "供应商“Unknown Supplier”不存在，请检查名称或先在数据管理中新增",
    ]
