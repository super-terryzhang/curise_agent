"""Normalization and validation for editable supplier Excel contracts."""

from __future__ import annotations

import re
from typing import Any

from openpyxl.cell.cell import MergedCell

_CELL_RE = re.compile(r"^[A-Z]{1,3}[1-9]\d*$")
_COL_RE = re.compile(r"^[A-Z]{1,3}$")

_PATH_ALIASES = {
    "order_data.ship_name": "ship_name",
    "order_data.po_number": "po_number",
    "order_data.order_date": "order_date",
    "order_data.delivery_date": "delivery_date",
    "order_data.delivery_address": "delivery_address",
    "order_data.destination_port": "destination_port",
    "order_data.voyage": "voyage",
    "order_data.invoice_number": "invoice_number",
    "order_data.currency": "currency",
    "suppliers.{sid}.supplier_name": "supplier_name",
    "suppliers.{sid}.supplier_info.contact": "supplier_contact",
    "suppliers.{sid}.supplier_info.phone": "supplier_tel",
    "suppliers.{sid}.supplier_info.fax": "supplier_fax",
    "suppliers.{sid}.supplier_info.email": "supplier_email",
    "suppliers.{sid}.supplier_info.address": "supplier_address",
    "suppliers.{sid}.supplier_info.zip_code": "supplier_zip_code",
    "suppliers.{sid}.supplier_info.default_payment_terms": "payment_date",
    "suppliers.{sid}.supplier_info.default_payment_method": "payment_method",
    "delivery_location.contact_person": "delivery_contact",
    "delivery_location.delivery_notes": "delivery_time_notes",
}


def canonical_metadata_key(path: str) -> str:
    value = str(path or "").strip()
    return _PATH_ALIASES.get(value, value.rsplit(".", 1)[-1])


def normalized_contract(template: Any) -> dict[str, Any]:
    """Merge legacy columns with the richer editable zone contract."""

    table = dict(template.product_table_config or {})
    styles = dict(template.template_styles or {})
    zones = styles.get("zones") if isinstance(styles.get("zones"), dict) else {}
    product_zone = (
        zones.get("product_data") if isinstance(zones.get("product_data"), dict) else {}
    )
    summary_zone = zones.get("summary") if isinstance(zones.get("summary"), dict) else {}
    columns = styles.get("product_columns")
    if not isinstance(columns, dict) or not columns:
        columns = table.get("columns") or {}
    product_formulas = styles.get("product_row_formulas")
    if not isinstance(product_formulas, dict):
        product_formulas = {}
    formula_columns = table.get("formula_columns") or list(product_formulas)
    start_row = product_zone.get("start", table.get("start_row", 12))
    return {
        "styles": styles,
        "zones": zones,
        "product_start": int(start_row),
        "product_end": int(product_zone["end"]) if product_zone.get("end") else None,
        "summary_start": int(summary_zone["start"]) if summary_zone.get("start") else None,
        "summary_end": int(summary_zone["end"]) if summary_zone.get("end") else None,
        "columns": {str(k).upper(): str(v) for k, v in columns.items()},
        "formula_columns": {str(col).upper() for col in formula_columns},
        "product_row_formulas": {
            str(k).upper(): str(v) for k, v in product_formulas.items()
        },
        "is_dynamic": bool(
            product_zone.get("start")
            and product_zone.get("end")
            and summary_zone.get("start")
            and summary_zone.get("end")
        ),
    }


def configuration_errors(
    *,
    field_positions: dict[str, Any] | None,
    product_table_config: dict[str, Any] | None,
    template_styles: dict[str, Any] | None,
) -> list[str]:
    """Return deterministic, user-facing configuration errors."""

    errors: list[str] = []
    positions = field_positions or {}
    for key, value in positions.items():
        info = value if isinstance(value, dict) else {"position": value}
        position = str(info.get("position") or "").upper()
        if not _CELL_RE.fullmatch(position):
            errors.append(f"字段 {key} 的单元格位置无效：{position or '空'}")

    table = product_table_config or {}
    columns = table.get("columns") or {}
    for col, field in columns.items():
        if not _COL_RE.fullmatch(str(col).upper()):
            errors.append(f"商品列无效：{col}")
        if not str(field).strip():
            errors.append(f"商品列 {col} 未选择字段")
    try:
        if int(table.get("start_row", 1)) < 1:
            errors.append("商品起始行必须大于 0")
    except (TypeError, ValueError):
        errors.append("商品起始行必须是整数")

    styles = template_styles or {}
    zones = styles.get("zones") if isinstance(styles.get("zones"), dict) else {}
    product_zone = zones.get("product_data")
    summary_zone = zones.get("summary")
    if isinstance(product_zone, dict) or isinstance(summary_zone, dict):
        if not isinstance(product_zone, dict) or not isinstance(summary_zone, dict):
            errors.append("动态模板必须同时配置商品区和汇总区")
        else:
            try:
                product_start = int(product_zone["start"])
                product_end = int(product_zone["end"])
                summary_start = int(summary_zone["start"])
                summary_end = int(summary_zone["end"])
                if not (0 < product_start <= product_end < summary_start <= summary_end):
                    errors.append("区域必须满足：商品开始 ≤ 商品结束 < 汇总开始 ≤ 汇总结束")
                if int(table.get("start_row", product_start)) != product_start:
                    errors.append("product_table_config.start_row 必须与商品区开始行一致")
            except (KeyError, TypeError, ValueError):
                errors.append("商品区和汇总区的开始/结束行必须是整数")

    header_fields = styles.get("header_fields") or {}
    for cell, path in header_fields.items():
        if not _CELL_RE.fullmatch(str(cell).upper()):
            errors.append(f"头部字段单元格无效：{cell}")
        if not isinstance(path, str) or not path.strip():
            errors.append(f"头部字段 {cell} 的数据来源不能为空")

    header_formats = styles.get("header_formats") or {}
    for cell, display_format in header_formats.items():
        if cell not in header_fields:
            errors.append(f"显示格式 {cell} 没有对应的头部字段")
        if not isinstance(display_format, str) or "{value}" not in display_format:
            errors.append(f"显示格式 {cell} 必须包含 {{value}}")

    product_formulas = styles.get("product_row_formulas") or {}
    for col, formula in product_formulas.items():
        col = str(col).upper()
        if not _COL_RE.fullmatch(col):
            errors.append(f"商品公式列无效：{col}")
        if not isinstance(formula, str) or not formula.startswith("=") or "{row}" not in formula:
            errors.append(f"商品公式列 {col} 必须是包含 {{row}} 的 Excel 公式")
        if col in {str(c).upper() for c in columns}:
            errors.append(f"商品公式列 {col} 不能同时映射普通字段")

    return errors


def assert_valid_configuration(template: Any) -> None:
    errors = configuration_errors(
        field_positions=template.field_positions,
        product_table_config=template.product_table_config,
        template_styles=template.template_styles,
    )
    if errors:
        raise ValueError("TEMPLATE_CONFIG_INVALID: " + "；".join(errors))


def assert_source_workbook_matches(ws: Any, template: Any) -> None:
    """Refuse to render when a configured contract targets a different file."""

    assert_valid_configuration(template)
    contract = (template.template_styles or {}).get("template_contract") or {}
    if not isinstance(contract, dict) or not contract:
        return
    errors: list[str] = []
    expected_sheet = contract.get("sheet_name")
    if expected_sheet and ws.title != expected_sheet:
        errors.append(f"工作表应为 {expected_sheet}，实际为 {ws.title}")
    expected_dimensions = contract.get("template_dimensions")
    if expected_dimensions and ws.calculate_dimension() != expected_dimensions:
        errors.append(
            f"模板范围应为 {expected_dimensions}，实际为 {ws.calculate_dimension()}"
        )
    merged = {str(item) for item in ws.merged_cells.ranges}
    for required in contract.get("required_merged_ranges") or []:
        if required not in merged:
            errors.append(f"缺少合并区域 {required}")
    for anchor in contract.get("anchors") or []:
        cell = str(anchor.get("cell") or "")
        if not _CELL_RE.fullmatch(cell):
            errors.append(f"模板锚点位置无效：{cell or '空'}")
            continue
        if isinstance(ws[cell], MergedCell) or ws[cell].value != anchor.get("value"):
            errors.append(f"模板锚点 {cell} 内容不匹配")
    if errors:
        raise ValueError("TEMPLATE_CONTRACT_MISMATCH: " + "；".join(errors))


__all__ = [
    "assert_source_workbook_matches",
    "assert_valid_configuration",
    "canonical_metadata_key",
    "configuration_errors",
    "normalized_contract",
]
