"""Hand-written expected values, independent of normalization implementation."""

from datetime import UTC, datetime
from importlib import import_module
from uuid import uuid4

import pytest

from domains.dynamic_data.errors import ValidationError
from domains.dynamic_data.schemas import FieldCreate, FieldResponse, FieldUpdate


def field(kind="text", **kwargs):
    return FieldResponse(
        id=uuid4(),
        table_id=uuid4(),
        label="测试字段",
        field_type=kind,
        status="active",
        sort_order=0,
        schema_version=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        **kwargs,
    )


def api():
    return import_module("domains.dynamic_data.validation")


def test_number_is_canonical_without_float():
    f = field("number")
    for value, expected in [
        ("1.00", "1"),
        (0, "0"),
        ("-0.000", "0"),
        ("123456789012345678", "123456789012345678"),
        ("0.000001", "0.000001"),
        ("1e2", "100"),
    ]:
        assert api().normalize_value(f, value) == expected


def test_false_is_required_boolean_value():
    assert api().normalize_value(field("boolean", required=True), False) is False


@pytest.mark.parametrize(
    "kind,value",
    [
        ("number", True),
        ("number", 1.5),
        ("number", "NaN"),
        ("number", "Infinity"),
        ("number", "0.1234567"),
        ("number", "1234567890123456789"),
        ("number", "1e999999"),
        ("number", "1e-999999"),
        ("date", "2026-13-40"),
        ("date", "2026-02-29"),
        ("date", "2026-1-2"),
        ("datetime", "2026-10-06T09:00:00"),
        ("boolean", "false"),
        ("link", "unknown"),
        ("text", 12),
    ],
)
def test_invalid_typed_values_return_field_issue(kind, value):
    f = field(kind)
    with pytest.raises(ValidationError) as exc:
        api().normalize_value(f, value)
    assert exc.value.issues[0].field_id == f.id
    assert exc.value.issues[0].field_label == f.label


def test_dates_and_text_do_not_lose_meaning():
    assert api().normalize_value(field("date"), "2026-10-06") == "2026-10-06"
    assert (
        api().normalize_value(field("datetime"), "2026-10-06T09:30:00+09:00")
        == "2026-10-06T00:30:00Z"
    )
    assert api().normalize_value(field(), " A ") == " A "
    assert api().normalize_value(field("link"), str(uuid4()))
    with pytest.raises(ValidationError):
        api().normalize_value(field(required=True), "   ")


def test_patch_missing_null_and_creation_default_are_distinct():
    f = field(default_value="初始")
    key = str(f.id)
    assert api().normalize_record([f], {}, create=True) == {key: "初始"}
    assert api().normalize_record([f], {key: None}, create=True) == {key: None}
    assert api().normalize_record([f], {}, existing={key: "已有"}, create=False) == {key: "已有"}
    assert api().normalize_record([f], {key: None}, existing={key: "已有"}, create=False) == {
        key: None
    }


def test_archived_field_preserved_but_cannot_be_explicitly_edited():
    f = field()
    f.status = "archived"
    k = str(f.id)
    assert api().normalize_record([f], {}, existing={k: "旧值"}, create=False) == {k: "旧值"}
    with pytest.raises(ValidationError):
        api().normalize_record([f], {k: "新值"}, existing={k: "旧值"}, create=False)


def test_inactive_options_only_preserved_when_unchanged():
    a, b, c = [str(uuid4()) for _ in range(3)]
    options = [
        {"id": a, "label": "旧项", "active": False},
        {"id": b, "label": "另一旧项", "active": False},
        {"id": c, "label": "启用项", "active": True},
    ]
    for kind, old, changed in [("single_select", a, b), ("multi_select", [a, c], [a, b])]:
        f = field(kind, config={"options": options})
        key = str(f.id)
        assert (
            api().normalize_record([f], {key: old}, existing={key: old}, create=False)[key] == old
        )
        with pytest.raises(ValidationError):
            api().normalize_record([f], {key: changed}, existing={key: old}, create=False)
        with pytest.raises(ValidationError):
            api().normalize_record([f], {key: old}, create=True)
    f = field("multi_select", config={"options": options})
    with pytest.raises(ValidationError):
        api().normalize_value(f, [c, c])
    with pytest.raises(ValidationError):
        api().normalize_value(field("multi_select", required=True, config={"options": options}), [])


def test_definition_config_and_option_limits():
    options = [{"id": str(uuid4()), "label": f"选项{i}"} for i in range(100)]
    request = FieldCreate(
        id=uuid4(),
        label="类型",
        field_type="single_select",
        config={"options": options},
        expected_schema_version=1,
    )
    assert len(api().validate_field_definition(request).config["options"]) == 100
    for bad in [options + [{"id": str(uuid4()), "label": "第101个"}], options[:1] * 2]:
        with pytest.raises(ValidationError):
            api().validate_field_definition(request.model_copy(update={"config": {"options": bad}}))
    for kind, config in [
        ("number", {"precision": 2, "scale": 3}),
        ("date", {"sql": "DROP TABLE"}),
        ("text", {"max_length": 4097}),
    ]:
        with pytest.raises(ValidationError):
            api().validate_field_definition(
                request.model_copy(update={"field_type": kind, "config": config})
            )


def test_unique_definition_and_default_validation():
    request = FieldCreate(
        id=uuid4(), label="编号", field_type="text", unique=True, expected_schema_version=1
    )
    assert api().validate_field_definition(request).config["max_length"] == 200
    for updated in [
        {"config": {"max_length": 201}},
        {"field_type": "date"},
        {"field_type": "link", "target_table_id": uuid4(), "default_value": str(uuid4())},
    ]:
        with pytest.raises(ValidationError):
            api().validate_field_definition(request.model_copy(update=updated))
    with pytest.raises(ValidationError):
        api().validate_field_definition(
            request.model_copy(
                update={"field_type": "number", "unique": False, "default_value": "not a number"}
            )
        )
    f = field(config={"max_length": 200}, unique=True)
    assert api().unique_value(f, "") == ""
    assert api().unique_value(f, " A ") == " A "
    assert api().unique_value(f, None) is None
    assert api().unique_value(field("number", unique=True), "1.00") == "1"


def test_definition_patch_preserves_identity_and_rejects_null_required():
    f = field()
    updated = api().validate_field_definition(
        FieldUpdate(label="新名", expected_schema_version=1), current=f
    )
    assert updated.id == f.id
    assert updated.label == "新名"
    with pytest.raises(ValidationError):
        api().validate_field_definition(
            FieldUpdate(required=None, expected_schema_version=1), current=f
        )


def test_unknown_fields_count_and_record_size_limits():
    fields = [field() for _ in range(100)]
    assert api().normalize_record(fields, {}, create=True) == {}
    with pytest.raises(ValidationError):
        api().normalize_record(fields + [field()], {}, create=True)
    with pytest.raises(ValidationError):
        api().normalize_record(fields, {str(uuid4()): "未知"}, create=True)
    with pytest.raises(ValidationError):
        api().normalize_record(
            fields[:17], {str(f.id): "x" * 4096 for f in fields[:17]}, create=True
        )


def test_tightened_precision_and_empty_values():
    with pytest.raises(ValidationError):
        api().normalize_value(field("number", config={"precision": 3, "scale": 1}), "12.34")
    assert api().normalize_value(field("number"), "") is None
    with pytest.raises(ValidationError):
        api().normalize_value(field("number", required=True), "")


@pytest.mark.parametrize(
    "kind,value,expected",
    [
        ("text", "说明", "说明"),
        ("number", "2.500", "2.5"),
        ("date", "2028-02-29", "2028-02-29"),
        ("datetime", "2026-10-06T00:00:00Z", "2026-10-06T00:00:00Z"),
        ("boolean", True, True),
    ],
)
def test_valid_scalar_values(kind, value, expected):
    assert api().normalize_value(field(kind), value) == expected


def test_valid_option_and_link_values():
    selected = str(uuid4())
    config = {"options": [{"id": selected, "label": "是", "active": True}]}
    assert api().normalize_value(field("single_select", config=config), selected) == selected
    assert api().normalize_value(field("multi_select", config=config), [selected]) == [selected]
    linked = str(uuid4())
    assert api().normalize_value(field("link"), linked) == linked


def test_all_required_errors_are_reported_together():
    fields = [field(required=True), field("boolean", required=True)]
    with pytest.raises(ValidationError) as exc:
        api().normalize_record(fields, {}, create=True)
    assert {issue.field_id for issue in exc.value.issues} == {f.id for f in fields}
