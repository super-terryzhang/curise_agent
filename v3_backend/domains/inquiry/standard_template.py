"""Canonical configuration for the Japanese inquiry workbook.

The workbook itself is stored through the normal file-storage abstraction.
This module owns only the declarative contract required to populate it.  Keeping
the contract in one place lets the local preview seed, tests and future release
bootstrap use identical field and formula definitions.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

STANDARD_TEMPLATE_NAME = "日本标准询价单"
# SupplierTemplate records keep using this runtime-storage key.  The canonical
# workbook itself is versioned under static/templates so clean checkouts and
# Docker builds do not depend on the ignored uploads directory.
STANDARD_TEMPLATE_FILE = "templates/询价单_template.xlsx"
STANDARD_TEMPLATE_ASSET = (
    Path(__file__).resolve().parents[2]
    / "static"
    / "templates"
    / "询价单_template.xlsx"
)


_FIELD_POSITIONS: dict[str, dict[str, str]] = {
    "ship_port_title": {
        "position": "A2",
        "description": "邮轮名称与目标港口",
    },
    "supplier_name": {"position": "A4", "description": "供应商名称"},
    "supplier_contact": {
        "position": "A5",
        "description": "供应商联系人",
        "format": "担当者：{value}",
    },
    "supplier_address": {"position": "A6", "description": "供应商地址"},
    "supplier_tel": {
        "position": "A7",
        "description": "供应商电话",
        "format": "TEL:{value}",
    },
    "supplier_email": {
        "position": "A8",
        "description": "供应商邮箱",
        "format": "E-mail:{value}",
    },
    "generated_date": {"position": "K4", "description": "询价单生成日期"},
    "inquiry_reference": {"position": "K5", "description": "询价单编号"},
    "voyage": {"position": "K6", "description": "航次"},
    "delivery_date": {"position": "H8", "description": "交货日期"},
    "delivery_time_notes": {"position": "K8", "description": "交货时间备注"},
    "delivery_address": {"position": "H9", "description": "交货地址"},
    "destination_port": {"position": "H10", "description": "目标港口"},
    "delivery_contact": {
        "position": "H11",
        "description": "交货联系人",
        "format": "担当：{value}",
    },
    "ship_name_alt": {
        "position": "I12",
        "source": "ship_name",
        "description": "船名备注",
        "format": "船名【{value}】",
    },
    "payment_date": {"position": "H13", "description": "付款期限"},
    "payment_method": {"position": "H14", "description": "付款方式"},
    "internal_contact": {"position": "H15", "description": "内部联系人及电话"},
}

_PRODUCT_TABLE_CONFIG: dict[str, Any] = {
    "start_row": 22,
    "columns": {
        "A": "line_number",
        "B": "po_number",
        "C": "product_code",
        "D": "product_name_en",
        "E": "product_name_jp",
        "F": "description",
        "H": "quantity",
        "I": "unit",
        "J": "unit_price",
        "K": "currency",
    },
    "formula_columns": ["L"],
}

_HEADER_FIELDS = {
    info["position"]: info.get("source", key) for key, info in _FIELD_POSITIONS.items()
}
_HEADER_FORMATS = {
    info["position"]: info["format"]
    for info in _FIELD_POSITIONS.values()
    if info.get("format")
}

_TEMPLATE_STYLES: dict[str, Any] = {
    "contract_version": 3,
    "zones": {
        "product_data": {"start": 22, "end": 32},
        "summary": {"start": 33, "end": 35},
    },
    "header_fields": _HEADER_FIELDS,
    "header_formats": _HEADER_FORMATS,
    "static_values": {
        "A1": "PURCHASE ORDER",
        "J5": "RFQ No.：",
    },
    "product_columns": _PRODUCT_TABLE_CONFIG["columns"],
    "product_row_merges": [{"start_col": 6, "end_col": 7}],
    "product_row_formulas": {"L": "=H{row}*J{row}"},
    "summary_formulas": [
        {"cell": "L33", "type": "product_sum", "label": "Sub Total"},
        {
            "cell": "L34",
            "type": "relative",
            "label": "Tax",
            "formula_template": "={sum_cell}*0.08",
        },
        {
            "cell": "L35",
            "type": "relative",
            "label": "GRAND TOTAL",
            "formula_template": "={sum_cell}+{tax_cell}",
        },
    ],
    "external_refs": [{"cell": "H16", "formula_template": "={grand_total_cell}"}],
    "summary_static_values": {
        "I33": "Sub Total",
        "I34": "Tax",
        "I35": "GRAND TOTAL",
        "K33": "JPY",
        "K34": "JPY",
        "K35": "JPY",
    },
    "stale_columns_in_summary": ["D", "I", "K"],
    "template_contract": {
        "version": 3,
        "sheet_name": "0804発注書 (ML)",
        "template_dimensions": "A1:Q46",
        "required_merged_ranges": [
            "A1:L1",
            "A2:L2",
            "A20:A21",
            "B20:B21",
            "C20:E20",
            "F20:G20",
            "F21:G21",
            "H20:I20",
            "H21:I21",
            "K20:L20",
            "K21:L21",
            "H16:J18",
        ],
        "anchors": [
            {"cell": "A1", "value": "PURCHASE ORDER"},
            {"cell": "F8", "value": "Delivery Date:"},
            {"cell": "F9", "value": "Delivery Address:"},
            {"cell": "J4", "value": "DATE："},
            {"cell": "J5", "value": "Invoice："},
            {"cell": "J6", "value": "Voyage："},
            {"cell": "C21", "value": "商品コード"},
            {"cell": "D21", "value": "英語表記"},
            {"cell": "E21", "value": "日本語表記"},
        ],
    },
}


def standard_template_definition() -> dict[str, Any]:
    """Return an isolated copy safe for ORM assignment and mutation."""

    return {
        "template_name": STANDARD_TEMPLATE_NAME,
        "template_file_url": STANDARD_TEMPLATE_FILE,
        "field_positions": deepcopy(_FIELD_POSITIONS),
        "product_table_config": deepcopy(_PRODUCT_TABLE_CONFIG),
        "template_styles": deepcopy(_TEMPLATE_STYLES),
    }


__all__ = [
    "STANDARD_TEMPLATE_ASSET",
    "STANDARD_TEMPLATE_FILE",
    "STANDARD_TEMPLATE_NAME",
    "standard_template_definition",
]
