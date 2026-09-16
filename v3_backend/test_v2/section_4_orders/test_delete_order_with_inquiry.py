"""Section 4 — orders: DELETE with cascade through Inquiry.

测试目标：
    Pin the cascade-delete contract introduced 2026-05-25 after a prod bug
    where deleting an order with a generated inquiry returned plain-text
    "Internal Server Error" — the SQLAlchemy IntegrityError escaped
    FastAPI's exception chain, the response had no CORS headers, and the
    browser surfaced it as a misleading "Failed to fetch".

    Contract:
      • No Inquiry → delete Order; cleanly succeeds.
      • Has Inquiry (+ supplier rows) → cascade: delete suppliers → delete
        Inquiry → delete Order. ALL related storage files cleaned best-effort.
      • Storage delete failures are logged, NOT propagated — the DB cascade
        always commits.
      • Repeat-delete on already-deleted order → NotFound (translates to
        404 at the HTTP layer).

为什么必须有：
    The bug pattern (Phase-2 stub left half-implemented, no integration
    test) was identical to the documents delete bug. Without these tests,
    any future refactor of `delete_order` can silently re-introduce the
    FK violation and the user-visible "Failed to fetch" mystery.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from domains.inquiry.models import Inquiry, InquirySupplier
from domains.orders import service as order_service
from domains.orders.errors import NotFound
from domains.orders.models import Order
from infrastructure.storage import get_storage, set_storage_for_tests


class _RecordingStorage:
    """In-memory storage stub — records every download/delete call."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self._files = dict(files or {})
        self.deleted: list[str] = []

    def upload(self, key: str, content: bytes, content_type: str | None = None) -> str:
        self._files[key] = content
        return key

    def download(self, key: str) -> bytes:
        return self._files[key]

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self._files.pop(key, None)

    def get_signed_url(self, key: str, expires_in: int = 3600) -> str:
        return f"mem://{key}"


def _seed_user(session_factory) -> int:
    from domains.identity.models import User
    from infrastructure.security import hash_password

    db = session_factory()
    try:
        u = User(
            email="del_order@example.com",
            hashed_password=hash_password("x"),
            full_name="X",
            role="employee",
            is_active=True,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _make_order(db, user_id: int, *, file_url: str | None = "orders/po.pdf") -> int:
    o = Order(
        user_id=user_id,
        filename="po.pdf",
        file_url=file_url,
        file_type="pdf",
        status="ready",
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return o.id


def _attach_inquiry(
    db,
    order_id: int,
    supplier_files: list[tuple[int, str | None]],
) -> int:
    inq = Inquiry(order_id=order_id, status="completed", supplier_count=len(supplier_files))
    db.add(inq)
    db.commit()
    db.refresh(inq)
    for sid, file_url in supplier_files:
        db.add(
            InquirySupplier(
                inquiry_id=inq.id,
                supplier_id=sid,
                supplier_name=f"S{sid}",
                status="completed" if file_url else "pending",
                excel_file_url=file_url,
            )
        )
    db.commit()
    return inq.id


@pytest.fixture
def user_id(session_factory) -> int:
    return _seed_user(session_factory)


# ─── T1: order without inquiry ─────────────────────────────────


def test_delete_order_without_inquiry_succeeds(session_factory, user_id) -> None:
    """Plain path — no related rows, just the Order + its PO file."""
    storage = _RecordingStorage({"orders/po.pdf": b"%PDF"})
    set_storage_for_tests(storage)
    try:
        db = session_factory()
        try:
            order_id = _make_order(db, user_id)
        finally:
            db.close()

        db = session_factory()
        try:
            order_service.delete_order(
                db, order_id=order_id, user_id=user_id, is_admin=False
            )
            assert db.get(Order, order_id) is None
        finally:
            db.close()

        assert "orders/po.pdf" in storage.deleted
    finally:
        set_storage_for_tests(None)


# ─── T2: order with inquiry + multiple suppliers ───────────────


def test_delete_order_cascades_through_inquiry_and_suppliers(
    session_factory, user_id
) -> None:
    """The actual prod bug: deleting an order with `v3_inquiries.order_id`
    referencing it crashed with FK violation. With the fix, supplier rows
    and the inquiry row are deleted first, then the order — and every
    associated file is cleaned up."""
    storage = _RecordingStorage(
        {
            "orders/po.pdf": b"%PDF",
            "inquiries/i_a.xlsx": b"A",
            "inquiries/i_b.xlsx": b"B",
        }
    )
    set_storage_for_tests(storage)
    try:
        db = session_factory()
        try:
            order_id = _make_order(db, user_id)
            inq_id = _attach_inquiry(
                db,
                order_id,
                supplier_files=[
                    (1, "inquiries/i_a.xlsx"),
                    (2, "inquiries/i_b.xlsx"),
                ],
            )
        finally:
            db.close()

        db = session_factory()
        try:
            order_service.delete_order(
                db, order_id=order_id, user_id=user_id, is_admin=False
            )
            # Everything in the chain is gone
            assert db.get(Order, order_id) is None
            assert db.get(Inquiry, inq_id) is None
            assert (
                db.query(InquirySupplier)
                .filter(InquirySupplier.inquiry_id == inq_id)
                .count()
                == 0
            )
        finally:
            db.close()

        # Storage cleanup hit every URL once
        assert sorted(storage.deleted) == sorted(
            ["orders/po.pdf", "inquiries/i_a.xlsx", "inquiries/i_b.xlsx"]
        )
    finally:
        set_storage_for_tests(None)


# ─── T3: suppliers with no excel_file_url (still generating) ───


def test_delete_order_handles_supplier_without_excel_url(
    session_factory, user_id
) -> None:
    """Suppliers in `pending`/`generating` state have no excel_file_url —
    the cascade must not crash on missing files, and only existing files
    are passed to storage.delete()."""
    storage = _RecordingStorage({"orders/po.pdf": b"%PDF"})
    set_storage_for_tests(storage)
    try:
        db = session_factory()
        try:
            order_id = _make_order(db, user_id)
            _attach_inquiry(
                db,
                order_id,
                supplier_files=[(1, None), (2, None)],
            )
        finally:
            db.close()

        db = session_factory()
        try:
            order_service.delete_order(
                db, order_id=order_id, user_id=user_id, is_admin=False
            )
            assert db.get(Order, order_id) is None
        finally:
            db.close()

        # Only the order's own PO file was eligible for cleanup
        assert storage.deleted == ["orders/po.pdf"]
    finally:
        set_storage_for_tests(None)


# ─── T4: storage failures are best-effort ─────────────────────


def test_delete_order_commits_even_when_storage_fails(
    session_factory, user_id
) -> None:
    """A flaky storage backend must NOT roll back the DB cascade — orphaned
    blobs are cheap, a stuck Order row is what the user notices."""

    class _BrokenStorage(_RecordingStorage):
        def delete(self, key: str) -> None:
            self.deleted.append(key)
            raise RuntimeError("simulated GCS outage")

    storage = _BrokenStorage(
        {
            "orders/po.pdf": b"%PDF",
            "inquiries/i_a.xlsx": b"A",
        }
    )
    set_storage_for_tests(storage)
    try:
        db = session_factory()
        try:
            order_id = _make_order(db, user_id)
            _attach_inquiry(db, order_id, supplier_files=[(1, "inquiries/i_a.xlsx")])
        finally:
            db.close()

        db = session_factory()
        try:
            order_service.delete_order(
                db, order_id=order_id, user_id=user_id, is_admin=False
            )
            assert db.get(Order, order_id) is None
        finally:
            db.close()

        # Both deletes were attempted despite each one raising
        assert sorted(storage.deleted) == sorted(
            ["orders/po.pdf", "inquiries/i_a.xlsx"]
        )
    finally:
        set_storage_for_tests(None)


# ─── T5: deleting non-existent order ──────────────────────────


def test_delete_order_missing_raises_not_found(session_factory, user_id) -> None:
    """Idempotent re-delete (double-click, retry) maps to NotFound, which
    the HTTP layer translates to 404 — never a 500."""
    set_storage_for_tests(_RecordingStorage())
    try:
        db = session_factory()
        try:
            with pytest.raises(NotFound):
                order_service.delete_order(
                    db, order_id=999999, user_id=user_id, is_admin=False
                )
        finally:
            db.close()
    finally:
        set_storage_for_tests(None)
