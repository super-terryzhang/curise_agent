"""Authoritative pure validation; no database or user-interface dependencies."""

import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError

from .errors import ValidationError
from .schemas import (
    Contract,
    ErrorIssue,
    FieldCreate,
    FieldDefinition,
    FieldResponse,
    FieldUpdate,
    NumberConfig,
    SelectConfig,
    TextConfig,
)

MAX_FIELDS = 100
MAX_RECORD_BYTES = 64 * 1024
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$", re.ASCII)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$", re.ASCII)
_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$", re.ASCII
)


def fail(field: FieldDefinition, code: str, message: str) -> None:
    raise ValidationError(
        code,
        message,
        [
            ErrorIssue(
                table_id=getattr(field, "table_id", None),
                field_id=field.id,
                field_label=field.label,
                code=code,
                message=message,
            )
        ],
    )


def _config(field: FieldDefinition) -> dict:
    cls = {
        "text": TextConfig,
        "number": NumberConfig,
        "single_select": SelectConfig,
        "multi_select": SelectConfig,
    }.get(field.field_type, Contract)
    raw = dict(field.config)
    if field.field_type == "text" and field.unique and "max_length" not in raw:
        raw["max_length"] = 200
    try:
        normalized = cls.model_validate(raw).model_dump(mode="json")
    except PydanticValidationError:
        fail(field, "INVALID_FIELD_CONFIG", "字段配置不合法，请检查长度、数字精度或选择项设置")
    if field.unique and field.field_type == "text" and normalized["max_length"] > 200:
        fail(field, "UNIQUE_TEXT_TOO_LONG", "唯一文本的最大长度不能超过 200 字符")
    if field.field_type in {"single_select", "multi_select"}:
        ids = [o["id"] for o in normalized["options"]]
        if len(ids) != len(set(ids)):
            fail(field, "DUPLICATE_OPTION_ID", "选择项编号不能重复")
    return normalized


def validate_field_definition(
    field: FieldCreate | FieldUpdate,
    current: FieldResponse | None = None,
) -> FieldDefinition:
    data = current.model_dump(include=set(FieldDefinition.model_fields)) if current else {}
    data.update(field.model_dump(exclude={"expected_schema_version"}, exclude_unset=True))
    try:
        definition = FieldDefinition.model_validate(data)
    except PydanticValidationError as exc:
        raise ValidationError("INVALID_FIELD_DEFINITION", "字段定义不完整或包含无效值") from exc
    if definition.unique and definition.field_type not in {"text", "number"}:
        fail(definition, "INVALID_UNIQUE_TYPE", "只有文本和数字可以设置唯一")
    if (definition.field_type == "link") != (definition.target_table_id is not None):
        fail(definition, "INVALID_LINK_TARGET", "关联字段必须配置目标表，其他类型不能配置目标表")
    if definition.field_type == "link" and definition.default_value is not None:
        fail(definition, "LINK_DEFAULT_FORBIDDEN", "关联字段不能设置默认值")
    definition.config = _config(definition)
    if definition.default_value is not None:
        definition.default_value = normalize_value(definition, definition.default_value)
    return definition


def _number(field: FieldDefinition, value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        fail(field, "INVALID_NUMBER", "数字必须使用整数或十进制文本，不能是布尔值或浮点数")
    raw = str(value)
    if len(raw) > 128 or not _NUMBER.fullmatch(raw):
        fail(field, "INVALID_NUMBER", "请填写合法数字，不能是 NaN、无穷或其他文本")
    try:
        number = Decimal(raw)
    except InvalidOperation:
        fail(field, "INVALID_NUMBER", "请填写合法数字")
    if not number.is_finite():
        fail(field, "INVALID_NUMBER", "请填写有限数字")
    config = NumberConfig.model_validate(field.config)
    # Check exponents BEFORE formatting. Tiny/huge exponents must not allocate
    # millions of zeroes; strip fractional trailing zeroes without Decimal's
    # context-limited normalize(), which could round large inputs.
    sign, digits, exponent = number.as_tuple()
    if not any(digits):
        return "0"
    digits = list(digits)
    while exponent < 0 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    scale = max(0, -exponent)
    significant = len(digits) + max(0, exponent)
    if (
        scale > config.scale
        or significant > config.precision
        or number.adjusted() >= config.precision
    ):
        fail(
            field,
            "NUMBER_PRECISION",
            f"数字最多 {config.precision} 位有效数字、{config.scale} 位小数",
        )
    canonical = format(number, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    return canonical


def normalize_value(
    field: FieldDefinition,
    value: Any,
    *,
    allow_inactive_option_ids: set[str] | None = None,
) -> Any:
    if value is None or (field.field_type == "number" and value == ""):
        if field.required:
            fail(field, "REQUIRED", "此字段为必填，请填写内容")
        return None
    kind = field.field_type
    if kind == "text":
        if not isinstance(value, str):
            fail(field, "INVALID_TEXT", "请填写文本")
        if field.required and not value.strip():
            fail(field, "REQUIRED", "必填文本不能只有空格")
        maximum = TextConfig.model_validate(field.config).max_length
        if len(value) > maximum:
            fail(field, "TEXT_TOO_LONG", f"文本不能超过 {maximum} 字符")
        return value
    if kind == "number":
        return _number(field, value)
    if kind == "boolean":
        if type(value) is not bool:
            fail(field, "INVALID_BOOLEAN", "请选择是或否")
        return value
    if kind == "date":
        try:
            if not isinstance(value, str) or not _DATE.fullmatch(value):
                raise ValueError
            return date.fromisoformat(value).isoformat()
        except ValueError:
            fail(field, "INVALID_DATE", "日期必须是有效的 YYYY-MM-DD 日期")
    if kind == "datetime":
        try:
            if not isinstance(value, str) or not _DATETIME.fullmatch(value):
                raise ValueError
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return instant.astimezone(UTC).isoformat().replace("+00:00", "Z")
        except (ValueError, OverflowError):
            fail(
                field,
                "INVALID_DATETIME",
                "日期时间必须有效且包含时区，例如 2026-10-06T09:00:00+09:00",
            )
    if kind == "link":
        try:
            if not isinstance(value, (str, UUID)):
                raise ValueError
            return str(UUID(str(value)))
        except ValueError:
            fail(field, "INVALID_LINK", "请选择有效的关联记录")
    options = {str(o["id"]): o for o in field.config.get("options", [])}
    selection = [value] if kind == "single_select" else value
    if not isinstance(selection, list) or len(selection) > 100:
        fail(field, "INVALID_SELECTION", "请选择最多 100 个有效选项")
    if any(not isinstance(v, str) for v in selection) or len(selection) != len(set(selection)):
        fail(field, "INVALID_SELECTION", "选择项编号必须有效且不能重复")
    if field.required and not selection:
        fail(field, "REQUIRED", "请至少选择一项")
    for selected in selection:
        option = options.get(selected)
        if option is None:
            fail(field, "UNKNOWN_OPTION", "选择项不存在于此字段")
        if not option.get("active", True) and selected not in (allow_inactive_option_ids or set()):
            fail(field, "INACTIVE_OPTION", "此选择项已停用，请选择启用的选项")
    return value


def normalize_record(
    fields: list[FieldResponse],
    values: dict[str, Any],
    *,
    existing: dict[str, Any] | None = None,
    create: bool,
) -> dict[str, Any]:
    if len(fields) > MAX_FIELDS:
        raise ValidationError("FIELD_LIMIT", "每张表最多 100 个字段")
    known = {str(f.id): f for f in fields}
    unknown = set(values) - set(known)
    if unknown:
        raise ValidationError(
            "UNKNOWN_FIELD",
            "提交了不存在的字段",
            [
                ErrorIssue(code="UNKNOWN_FIELD", message=f"字段 {key} 不存在")
                for key in sorted(unknown)
            ],
        )
    original = existing or {}
    result = dict(original)
    issues = []
    for key, f in known.items():
        if f.status == "archived":
            if key in values:
                issues.append(
                    ErrorIssue(
                        table_id=f.table_id,
                        field_id=f.id,
                        field_label=f.label,
                        code="ARCHIVED_FIELD",
                        message="字段已归档，不能修改",
                    )
                )
            continue
        value = values.get(key, f.default_value if create else original.get(key))
        inactive = set()
        if not create and value == original.get(key):
            previous = (
                value if f.field_type == "multi_select" and isinstance(value, list) else [value]
            )
            inactive = {v for v in previous if isinstance(v, str)}
        try:
            normalized = normalize_value(f, value, allow_inactive_option_ids=inactive)
            if key in values or key in original or (create and f.default_value is not None):
                result[key] = normalized
        except ValidationError as exc:
            issues.extend(exc.issues)
    if issues:
        raise ValidationError("INVALID_RECORD", "记录存在字段问题，请修正后保存", issues)
    ordinary = {k: v for k, v in result.items() if k not in known or known[k].field_type != "link"}
    if (
        len(json.dumps(ordinary, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        > MAX_RECORD_BYTES
    ):
        raise ValidationError("RECORD_TOO_LARGE", "一条记录的内容不能超过 64 KiB")
    return result


def unique_value(field: FieldDefinition, value: Any) -> str | None:
    if not field.unique or value is None:
        return None
    canonical = normalize_value(field, value)
    if field.field_type == "text" and len(canonical) > 200:
        fail(field, "UNIQUE_TEXT_TOO_LONG", "唯一文本不能超过 200 字符")
    return canonical
