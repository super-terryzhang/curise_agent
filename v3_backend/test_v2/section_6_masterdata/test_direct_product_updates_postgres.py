"""Identity concurrency checks against an explicitly disposable local database."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, local

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from domains.masterdata.models import Product
from domains.masterdata.upload import commit_validated_batch
from domains.masterdata.upload.direct_updates import lock_identity_writes
from domains.masterdata.upload.errors import BatchInWrongState, BatchValidationFailed
from infrastructure.db.base import Base
from test_v2.fixtures.helpers import seed_product, seed_user
from test_v2.section_6_masterdata.test_direct_product_updates import _prepare, _row

pytestmark = pytest.mark.skipif(
    not os.environ.get("DIRECT_UPDATE_TEST_POSTGRES_URL"),
    reason="explicit disposable direct-edit PostgreSQL required",
)


@pytest.fixture
def direct_engine():
    url = make_url(os.environ["DIRECT_UPDATE_TEST_POSTGRES_URL"])
    assert (
        url.host == "127.0.0.1"
        and url.port == 55446
        and url.database == "cruise_batch_edit_validation"
    )
    engine = create_engine(url, pool_size=4)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_identity_lock_is_transactional_postgres_table_lock(direct_engine):
    with Session(direct_engine) as db:
        lock_identity_writes(db)
        assert db.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() "
                "AND relation='products'::regclass AND mode='ShareRowExclusiveLock' AND granted)"
            )
        )
        db.rollback()
        assert not db.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() "
                "AND relation='products'::regclass AND mode='ShareRowExclusiveLock' AND granted)"
            )
        )


def test_two_simultaneous_identity_edits_cannot_create_duplicate_identity(direct_engine):
    with Session(direct_engine) as db:
        seed_user(db, email="concurrency@example.test")
        first = seed_product(db, code="A", name="A")
        second = seed_product(db, code="B", name="B")
        batches = [
            _prepare(db, [p], [_row(p, {"product_name_en": "X", "code": "X"})], "basic")["id"]
            for p in (first, second)
        ]
    barrier = Barrier(2)

    def save(batch_id):
        with Session(direct_engine) as db:
            barrier.wait(timeout=10)
            try:
                commit_validated_batch(db, batch_id=batch_id, user_id=1)
                return True
            except BatchValidationFailed:
                db.rollback()
                return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, batches))
    assert sum(results) == 1
    with Session(direct_engine) as db:
        products = list(db.scalars(select(Product)).all())
        assert sum(p.code == "X" and p.product_name_en == "X" for p in products) == 1


def test_cancel_waits_for_running_commit_and_cannot_overwrite_completed_state(
    direct_engine, monkeypatch
):
    from domains.masterdata.upload import service
    from domains.masterdata.upload.models import UploadBatch

    with Session(direct_engine) as db:
        seed_user(db, email="cancel-race@example.test")
        product = seed_product(db)
        batch_id = _prepare(db, [product], [_row(product, {"brand": "Saved brand"})], "basic")["id"]
    ready, release, cancel_read = Event(), Event(), Event()
    thread_state = local()
    update, load = service._apply_update, service._load_owned_batch

    def pausing_update(db, target, row, batch_id, user_id):
        ready.set()
        assert release.wait(timeout=10)
        return update(db, target, row, batch_id, user_id)

    def observed_load(db, *args, **kwargs):
        batch = load(db, *args, **kwargs)
        if getattr(thread_state, "cancelling", False):
            cancel_read.set()
        return batch

    monkeypatch.setattr(service, "_apply_update", pausing_update)
    monkeypatch.setattr(service, "_load_owned_batch", observed_load)

    def commit():
        with Session(direct_engine) as db:
            return commit_validated_batch(db, batch_id=batch_id, user_id=1)

    def cancel():
        thread_state.cancelling = True
        with Session(direct_engine) as db, pytest.raises(BatchInWrongState):
            service.cancel_batch(db, batch_id=batch_id, user_id=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        committing = pool.submit(commit)
        assert ready.wait(timeout=10), repr(committing.exception(timeout=1))
        cancelling = pool.submit(cancel)
        assert cancel_read.wait(timeout=10)
        release.set()
        assert committing.result(timeout=10)["updated"] == 1
        cancelling.result(timeout=10)
    with Session(direct_engine) as db:
        assert db.get(UploadBatch, batch_id).status == "completed"
