from uuid import uuid4

import pytest
from sqlalchemy import event

from domains.dynamic_data import service as s
from domains.dynamic_data.errors import ValidationError
from domains.dynamic_data.schemas import (
    Actor,
    ChangeQuery,
    FieldCreate,
    RecordCreate,
    RecordFilter,
    RecordQuery,
    TableCreate,
    TableUpdate,
)

A = Actor(id=1, role="admin")


def test_link_page_includes_batched_current_target_labels(data_db):
    db = data_db
    target, name = setup(db)
    s.update_table(
        db, target.id, TableUpdate(expected_schema_version=2, display_field_id=name.id), actor=A
    )
    dest = s.create_record(
        db,
        target.id,
        RecordCreate(id=uuid4(), schema_version=3, values={str(name.id): "真实目标名称"}),
        actor=A,
    )
    source = s.create_table(db, TableCreate(id=uuid4(), name="来源"), actor=A)
    link = s.create_field(
        db,
        source.id,
        FieldCreate(
            id=uuid4(),
            label="关联",
            field_type="link",
            target_table_id=target.id,
            expected_schema_version=1,
        ),
        actor=A,
    )
    for _ in range(10):
        s.create_record(
            db,
            source.id,
            RecordCreate(id=uuid4(), schema_version=2, values={str(link.id): str(dest.id)}),
            actor=A,
        )
    statements = []

    def count(*args):
        statements.append(args[2])

    event.listen(db.bind, "before_cursor_execute", count)
    try:
        page = s.list_records(db, source.id, RecordQuery())
    finally:
        event.remove(db.bind, "before_cursor_execute", count)
    assert page.items[0].linked_labels[str(link.id)]["display_label"] == "真实目标名称"
    assert page.items[0].linked_labels[str(link.id)]["record_id"] == str(dest.id)
    assert len(statements) <= 8


def setup(db, kind="text"):
    t = s.create_table(db, TableCreate(id=uuid4(), name="查询"), actor=A)
    f = s.create_field(
        db,
        t.id,
        FieldCreate(id=uuid4(), label="内容", field_type=kind, expected_schema_version=1),
        actor=A,
    )
    return t, f


def row(db, t, f, value):
    return s.create_record(
        db, t.id, RecordCreate(id=uuid4(), schema_version=2, values={str(f.id): value}), actor=A
    )


def test_numeric_order_is_two_before_ten(data_db):
    db = data_db
    t, f = setup(db, "number")
    row(db, t, f, "10")
    row(db, t, f, "2")
    row(db, t, f, None)
    page = s.list_records(db, t.id, RecordQuery(sort_field_id=f.id))
    assert [r.values.get(str(f.id)) for r in page.items] == ["2", "10", None]
    filtered = s.list_records(
        db, t.id, RecordQuery(filters=[RecordFilter(field_id=f.id, operator="gt", value="2")])
    )
    assert [r.values[str(f.id)] for r in filtered.items] == ["10"]


def test_duplicate_display_names_keep_separate_ids(data_db):
    db = data_db
    t, f = setup(db)
    s.update_table(db, t.id, TableUpdate(expected_schema_version=2, display_field_id=f.id), actor=A)
    a, b = [
        s.create_record(
            db,
            t.id,
            RecordCreate(id=uuid4(), schema_version=3, values={str(f.id): "同名"}),
            actor=A,
        )
        for _ in range(2)
    ]
    page = s.list_records(db, t.id, RecordQuery())
    assert {r.id for r in page.items} == {a.id, b.id}
    assert {r.display_label for r in page.items} == {"同名"}


def test_pagination_counts_and_no_per_row_queries(data_db):
    db = data_db
    t, f = setup(db)
    for i in range(51):
        row(db, t, f, str(i))
    statements = []

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", count)
    try:
        first = s.list_records(db, t.id, RecordQuery())
        second = s.list_records(db, t.id, RecordQuery(page=2))
    finally:
        event.remove(db.bind, "before_cursor_execute", count)
    assert first.total == second.total == 51
    assert len(first.items) == 50 and len(second.items) == 1
    assert len({r.id for r in first.items + second.items}) == 51
    assert len(statements) <= 10
    tables = s.list_tables(db, status="active", page=1, page_size=50)
    assert tables.items[0].field_count == 1
    assert tables.items[0].record_count == 51


def test_filter_validation_and_bound_sql_text(data_db):
    db = data_db
    t, f = setup(db)
    row(db, t, f, "'; DROP TABLE v3_data_records; --")
    page = s.list_records(
        db,
        t.id,
        RecordQuery(filters=[RecordFilter(field_id=f.id, operator="contains", value="DROP TABLE")]),
    )
    assert page.total == 1
    for query in [
        RecordQuery(sort_field_id=uuid4()),
        RecordQuery(filters=[RecordFilter(field_id=f.id, operator="gt", value="a")]),
    ]:
        with pytest.raises(ValidationError):
            s.list_records(db, t.id, query)
    assert s.list_records(db, t.id, RecordQuery()).total == 1


def test_history_is_stably_newest_first(data_db):
    db = data_db
    t, f = setup(db)
    r = row(db, t, f, "历史")
    history = s.list_changes(db, t.id, ChangeQuery())
    assert history.items[0].entity_id == r.id
    assert history.total == 3


def test_cyclic_links_do_not_expand_recursively(data_db):
    db = data_db
    t = s.create_table(db, TableCreate(id=uuid4(), name="自身关联"), actor=A)
    f = s.create_field(
        db,
        t.id,
        FieldCreate(
            id=uuid4(),
            label="关联",
            field_type="link",
            target_table_id=t.id,
            expected_schema_version=1,
        ),
        actor=A,
    )
    r = s.create_record(db, t.id, RecordCreate(id=uuid4(), schema_version=2, values={}), actor=A)
    from domains.dynamic_data.schemas import RecordAction, RecordUpdate

    s.update_record(
        db,
        t.id,
        r.id,
        RecordUpdate(expected_revision=1, schema_version=2, values={str(f.id): str(r.id)}),
        actor=A,
    )
    page = s.list_records(db, t.id, RecordQuery())
    assert page.items[0].values == {str(f.id): str(r.id)}
    assert len(page.items[0].model_dump_json()) < 2000
    s.set_record_status(
        db, t.id, r.id, RecordAction(expected_revision=2, schema_version=2), active=False, actor=A
    )
    s.set_record_status(
        db, t.id, r.id, RecordAction(expected_revision=3, schema_version=2), active=True, actor=A
    )
    assert s.get_record(db, t.id, r.id).values == {str(f.id): str(r.id)}


@pytest.mark.parametrize(
    "kind,config,values,operator,filter_value,expected",
    [
        ("boolean", {}, [False, True, None], "eq", False, [False]),
        ("date", {}, ["2026-10-01", "2026-10-06", None], "gte", "2026-10-05", ["2026-10-06"]),
        (
            "datetime",
            {},
            ["2026-10-06T09:00:00+09:00", "2026-10-06T09:00:00.100000+09:00", None],
            "gt",
            "2026-10-06T00:00:00Z",
            ["2026-10-06T00:00:00.100000Z"],
        ),
    ],
)
def test_typed_filters(data_db, kind, config, values, operator, filter_value, expected):
    db = data_db
    t, f = setup(db, kind)
    for value in values:
        row(db, t, f, value)
    page = s.list_records(
        db,
        t.id,
        RecordQuery(filters=[RecordFilter(field_id=f.id, operator=operator, value=filter_value)]),
    )
    assert [r.values[str(f.id)] for r in page.items] == expected


def test_multiselect_contains_and_empty(data_db):
    db = data_db
    t = s.create_table(db, TableCreate(id=uuid4(), name="多选"), actor=A)
    option = str(uuid4())
    f = s.create_field(
        db,
        t.id,
        FieldCreate(
            id=uuid4(),
            label="多选",
            field_type="multi_select",
            config={"options": [{"id": option, "label": "项目"}]},
            expected_schema_version=1,
        ),
        actor=A,
    )
    for value in ([option], [], None):
        row(db, t, f, value)
    assert (
        s.list_records(
            db,
            t.id,
            RecordQuery(filters=[RecordFilter(field_id=f.id, operator="contains", value=option)]),
        ).total
        == 1
    )
    assert (
        s.list_records(
            db, t.id, RecordQuery(filters=[RecordFilter(field_id=f.id, operator="is_empty")])
        ).total
        == 2
    )


def test_link_picker_labels_ids_search_and_display_fallback(data_db):
    db = data_db
    t, name = setup(db)
    link = s.create_field(
        db,
        t.id,
        FieldCreate(
            id=uuid4(),
            label="同表联系",
            field_type="link",
            target_table_id=t.id,
            expected_schema_version=2,
        ),
        actor=A,
    )
    s.update_table(
        db, t.id, TableUpdate(expected_schema_version=3, display_field_id=name.id), actor=A
    )
    rows = [
        s.create_record(
            db,
            t.id,
            RecordCreate(id=uuid4(), schema_version=4, values={str(name.id): "同名"}),
            actor=A,
        )
        for _ in range(2)
    ]
    choices = s.search_link_targets(db, t.id, link.id, q="同名", page=1, page_size=50)
    assert {r.id for r in choices.items} == {r.id for r in rows}
    assert {r.display_label for r in choices.items} == {"同名"}
    exact = s.search_link_targets(db, t.id, link.id, q=str(rows[0].id), page=1, page_size=50)
    assert [r.id for r in exact.items] == [rows[0].id]
    assert (
        s.search_link_targets(db, t.id, link.id, q="'; DROP TABLE", page=1, page_size=50).total == 0
    )
    from domains.dynamic_data.schemas import SchemaAction

    s.set_field_status(
        db, t.id, name.id, SchemaAction(expected_schema_version=4), active=False, actor=A
    )
    fallback = s.search_link_targets(db, t.id, link.id, q="", page=1, page_size=50)
    assert {r.display_label for r in fallback.items} == {f"记录 {r.id}" for r in rows}
