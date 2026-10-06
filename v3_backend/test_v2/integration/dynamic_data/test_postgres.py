"""Real PostgreSQL races: removing locks/unique reservations must break these tests."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from domains.dynamic_data import service as s
from domains.dynamic_data.errors import Conflict, ValidationError
from domains.dynamic_data.models import DataChange, DataLink, DataRecord, DataUniqueValue
from domains.dynamic_data.repository import lock_tables
from domains.dynamic_data.schemas import (
    Actor,
    FieldCreate,
    RecordAction,
    RecordCreate,
    RecordFilter,
    RecordQuery,
    RecordUpdate,
    TableCreate,
    TableUpdate,
)

A = Actor(id=1, role="admin")


def setup(engine, kind="text", **kwargs):
    with Session(engine) as db:
        t = s.create_table(db, TableCreate(id=uuid4(), name="真实数据库"), actor=A)
        f = s.create_field(
            db,
            t.id,
            FieldCreate(
                id=uuid4(), label="值", field_type=kind, expected_schema_version=1, **kwargs
            ),
            actor=A,
        )
        return t, f


def insert(engine, t, f, value, record_id=None):
    with Session(engine) as db:
        return s.create_record(
            db,
            t.id,
            RecordCreate(id=record_id or uuid4(), schema_version=2, values={str(f.id): value}),
            actor=A,
        )


def test_same_unique_value_has_one_winner(pg_data_engine):
    e = pg_data_engine
    t, f = setup(e, unique=True)
    ready = Barrier(2)

    def before_insert(*args):
        ready.wait(timeout=10)

    event.listen(DataUniqueValue, "before_insert", before_insert)

    def run():
        try:
            insert(e, t, f, "同一值")
            return True
        except Conflict:
            return False

    try:
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: run(), range(2)))
    finally:
        event.remove(DataUniqueValue, "before_insert", before_insert)
    assert sum(results) == 1
    with Session(e) as db:
        assert db.scalar(select(func.count()).select_from(DataRecord)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(DataChange)
                .where(DataChange.action == "create_record")
            )
            == 1
        )


def test_schema_writer_waits_for_record_reader(pg_data_engine):
    e = pg_data_engine
    t, _ = setup(e)
    entered, finished = Event(), Event()
    with Session(e) as reader:
        lock_tables(reader, [t.id], exclusive_ids=set())

        def run():
            with Session(e) as writer:
                writer.execute(text("SET LOCAL lock_timeout = '5s'"))
                pid = writer.scalar(text("SELECT pg_backend_pid()"))
                entered.pid = pid
                entered.set()
                result = s.update_table(
                    writer, t.id, TableUpdate(expected_schema_version=2, name="更新结构"), actor=A
                )
                finished.set()
                return result

        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(run)
            assert entered.wait(5)
            # PostgreSQL itself proves blocking; no sleep guesses.
            with e.connect() as observer:
                for _ in range(2000):
                    if observer.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid))"), {"pid": entered.pid}
                    ):
                        break
                else:
                    pytest.fail("writer never waited for reader lock")
            assert not finished.is_set()
            reader.commit()
            assert future.result(10).name == "更新结构"


def test_two_updates_cannot_overwrite_revision(pg_data_engine):
    e = pg_data_engine
    t, f = setup(e)
    r = insert(e, t, f, "旧值")
    ready = Barrier(2)

    def run(value):
        with Session(e) as db:
            ready.wait(10)
            try:
                s.update_record(
                    db,
                    t.id,
                    r.id,
                    RecordUpdate(expected_revision=1, schema_version=2, values={str(f.id): value}),
                    actor=A,
                )
                return True
            except Conflict:
                return False

    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(run, ["甲", "乙"])) == 1
    with Session(e) as db:
        assert db.get(DataRecord, r.id).revision == 2
        assert (
            db.scalar(
                select(func.count())
                .select_from(DataChange)
                .where(DataChange.action == "update_record")
            )
            == 1
        )


def test_link_save_and_target_archive_cannot_both_succeed(pg_data_engine):
    e = pg_data_engine
    target, name = setup(e)
    target_row = insert(e, target, name, "关联目标")
    source, link = setup(e, "link", target_table_id=target.id)
    ready = Barrier(2)

    def run(archive):
        ready.wait(10)
        try:
            if archive:
                with Session(e) as db:
                    s.set_record_status(
                        db,
                        target.id,
                        target_row.id,
                        RecordAction(expected_revision=1, schema_version=2),
                        active=False,
                        actor=A,
                    )
            else:
                insert(e, source, link, str(target_row.id))
            return True
        except ValidationError:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(run, [False, True])) == 1


def test_postgres_numeric_boolean_and_multiselect_null_queries(pg_data_engine):
    e = pg_data_engine
    t, f = setup(e, "number")
    for value in ["10", "2", None]:
        insert(e, t, f, value)
    with Session(e) as db:
        assert [
            r.values[str(f.id)]
            for r in s.list_records(db, t.id, RecordQuery(sort_field_id=f.id)).items
        ] == ["2", "10", None]
        plan = (
            db.execute(
                text(
                    "EXPLAIN SELECT id FROM v3_data_records WHERE table_id = :t "
                    "AND status = 'active' ORDER BY CAST(values ->> :f AS NUMERIC), id LIMIT 50"
                ),
                {"t": t.id, "f": str(f.id)},
            )
            .scalars()
            .all()
        )
        assert any("Sort" in line for line in plan)
    multi, mf = setup(e, "multi_select", config={"options": []})
    insert(e, multi, mf, [])
    insert(e, multi, mf, None)
    with Session(e) as db:
        assert (
            s.list_records(
                db,
                multi.id,
                RecordQuery(filters=[RecordFilter(field_id=mf.id, operator="is_empty")]),
            ).total
            == 2
        )
    boolean, bf = setup(e, "boolean")
    insert(e, boolean, bf, False)
    insert(e, boolean, bf, True)
    with Session(e) as db:
        assert (
            s.list_records(
                db,
                boolean.id,
                RecordQuery(filters=[RecordFilter(field_id=bf.id, operator="eq", value=False)]),
            ).total
            == 1
        )


def test_same_creation_uuid_is_idempotent_under_concurrency(pg_data_engine):
    e = pg_data_engine
    t, f = setup(e)
    record_id, ready = uuid4(), Barrier(2)

    def run(_):
        ready.wait(10)
        return insert(e, t, f, "重试", record_id).id

    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(run, range(2))) == [record_id, record_id]
    with Session(e) as db:
        assert db.scalar(select(func.count()).select_from(DataRecord)) == 1


def test_composite_foreign_key_rejects_valid_record_in_wrong_target_table(pg_data_engine):
    e = pg_data_engine
    target, tf = setup(e)
    other, of = setup(e)
    wrong = insert(e, other, of, "错误目标表")
    source, lf = setup(e, "link", target_table_id=target.id)
    source_row = insert(e, source, lf, None)
    with Session(e) as db:
        db.add(
            DataLink(
                table_id=source.id,
                record_id=source_row.id,
                field_id=lf.id,
                target_table_id=target.id,
                target_record_id=wrong.id,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert db.scalar(select(func.count()).select_from(DataLink)) == 0


@pytest.mark.parametrize(
    "kind, values, cutoff",
    [
        ("date", ["2026-10-01", "2026-10-03"], "2026-10-02"),
        ("datetime", ["2026-10-01T00:00:00Z", "2026-10-03T09:00:00+09:00"], "2026-10-02T00:00:00Z"),
    ],
)
def test_postgres_temporal_ranges(pg_data_engine, kind, values, cutoff):
    t, f = setup(pg_data_engine, kind)
    for value in values:
        insert(pg_data_engine, t, f, value)
    with Session(pg_data_engine) as db:
        assert (
            s.list_records(
                db,
                t.id,
                RecordQuery(filters=[RecordFilter(field_id=f.id, operator="gt", value=cutoff)]),
            ).total
            == 1
        )
