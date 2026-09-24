"""Order issue read model: many findings, one row, deterministic actions."""

from domains.orders.issues import build_issue_overview
from domains.orders.models import Order


def _order(order_id=1, *, products=None, match_results=None, anomaly_data=None):
    return Order(
        id=order_id,
        user_id=1,
        filename=f"po-{order_id}.pdf",
        status="ready",
        po_number=f"PO-{order_id}",
        loading_date="2026-10-05",
        port_id=1,
        products=products or [],
        match_results=match_results or [],
        anomaly_data=anomaly_data or {},
    )


def _matched_row(*, line_id, name="FROZEN VEGETABLE MIX"):
    return {
        "line_id": line_id,
        "product_code": "88PRD20418",
        "product_name": name,
        "quantity": 9,
        "unit": "CA24.0",
        "unit_price": 12,
        "match_status": "matched",
        "match_score": 1.0,
        "match_reason": "产品代码完全匹配",
        "matched_product": {
            "id": 91,
            "code": "88PRD20418",
            "product_name_en": name,
            "supplier_id": 7,
            "unit": "CA",
            "purchase_price_period": {},
            "selling_price_period": {},
        },
    }


def test_overview_keeps_all_findings_but_counts_one_actionable_row():
    row = _matched_row(line_id="line-1")
    order = _order(
        products=[dict(row)],
        match_results=[row],
        anomaly_data={
            "findings": [
                {
                    "code": "UNIT_CONVERSION_REQUIRED",
                    "severity": "error",
                    "scope": "row",
                    "line_id": "line-1",
                    "row_index": 1,
                    "message": "单位不一致",
                    "suggestion": "登记换算依据",
                    "evidence": {"source_unit": "CA24.0", "supplier_unit": "CA"},
                },
                {
                    "code": "SELLING_PRICE_DEVIATION",
                    "severity": "warning",
                    "scope": "row",
                    "line_id": "line-1",
                    "row_index": 1,
                    "message": "卖价偏差",
                    "suggestion": "核对卖价期间",
                    "evidence": {},
                },
            ]
        },
    )

    overview = build_issue_overview(order, [order])

    assert overview["actionable_row_count"] == 1
    assert len(overview["rows"]) == 1
    issue_row = overview["rows"][0]
    assert {item["code"] for item in issue_row["findings"]} == {
        "UNIT_CONVERSION_REQUIRED",
        "SELLING_PRICE_DEVIATION",
    }
    assert issue_row["match_status"] == "matched"
    assert issue_row["inquiry_disposition"] == "excluded"
    assert issue_row["findings"][0]["resolution"]["target"] == "unit_conversion"


def test_overview_uses_row_identity_not_duplicate_product_name():
    first = _matched_row(line_id="line-1", name="SAME NAME")
    second = _matched_row(line_id="line-2", name="SAME NAME")
    order = _order(
        products=[dict(first), dict(second)],
        match_results=[first, second],
        anomaly_data={
            "findings": [
                {
                    "code": "CUSTOM_RULE",
                    "severity": "error",
                    "scope": "row",
                    "row_index": 2,
                    "product_name": "SAME NAME",
                    "message": "second only",
                    "suggestion": "review",
                    "evidence": {"raw": "value"},
                }
            ]
        },
    )

    overview = build_issue_overview(order, [order])

    assert overview["rows"][0]["findings"] == []
    assert overview["rows"][1]["findings"][0]["code"] == "CUSTOM_RULE"
    assert overview["rows"][1]["findings"][0]["resolution"] == {
        "target": "review",
        "label": "查看并人工处理",
    }


def test_overview_attributes_cross_po_findings_to_source_order():
    target_row = _matched_row(line_id="target-line")
    target = _order(products=[dict(target_row)], match_results=[target_row])
    owner = _order(
        2,
        anomaly_data={
            "findings": [
                {
                    "code": "RFQ_ROW_EXCLUDED",
                    "severity": "error",
                    "scope": "row",
                    "source_order_id": 1,
                    "line_id": "target-line",
                    "message": "该行没有进入询价",
                    "suggestion": "处理后生成新版本",
                    "evidence": {},
                }
            ]
        },
    )

    overview = build_issue_overview(target, [target, owner])

    assert [item["code"] for item in overview["rows"][0]["findings"]] == [
        "RFQ_ROW_EXCLUDED"
    ]
    assert overview["actionable_row_count"] == 1


def test_overview_treats_legacy_possible_match_as_unmatched():
    result = {
        "product_name": "LEGACY",
        "product_code": "L-1",
        "quantity": 1,
        "unit": "EA",
        "match_status": "possible_match",
        "match_reason": "旧版候选匹配",
        "matched_product": {"id": 10, "supplier_id": 1, "unit": "EA"},
    }
    order = _order(products=[dict(result)], match_results=[result])

    overview = build_issue_overview(order, [order])

    assert overview["rows"][0]["match_status"] == "not_matched"
    assert overview["rows"][0]["inquiry_disposition"] == "excluded"
    assert overview["rows"][0]["findings"][0]["resolution"]["target"] == "product_match"


def test_overview_does_not_duplicate_stored_unmatched_finding_without_row_index():
    result = {
        "line_id": "stable-line",
        "product_name": "UNMATCHED",
        "quantity": 1,
        "unit": "EA",
        "match_status": "not_matched",
        "match_reason": "没有匹配上",
        "matched_product": None,
    }
    order = _order(
        products=[dict(result)],
        match_results=[result],
        anomaly_data={
            "findings": [
                {
                    "code": "PRODUCT_NOT_MATCHED",
                    "severity": "error",
                    "scope": "row",
                    "line_id": "stable-line",
                    "message": "没有匹配上",
                    "suggestion": "人工关联",
                    "evidence": {},
                }
            ]
        },
    )

    overview = build_issue_overview(order, [order])

    assert [item["code"] for item in overview["rows"][0]["findings"]] == [
        "PRODUCT_NOT_MATCHED"
    ]
