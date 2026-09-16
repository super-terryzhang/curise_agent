"""Section 3 — documents: DELETE with linked-order semantics.

测试目标：
    Pin the contract introduced 2026-05-25 after a prod bug where
    `DELETE /api/documents/{id}?force=true` would 500 with a
    ForeignKeyViolation. The legacy code accepted the `force` param but
    never actually unlinked the referencing Order rows, so the database
    refused the DELETE.

    Contract:
      • No linked Orders → delete unconditionally.
      • Linked Orders + force=False → raise BadRequest (HTTP 400) with
        a message listing the linked order IDs.
      • Linked Orders + force=True → set every Order.document_id to NULL,
        FLUSH so the FK is released, then delete the document. The Orders
        themselves are preserved.
      • Storage cleanup failure → logged, NOT propagated; DB delete still
        commits (file is cheap to GC later).

为什么必须有：
    Without these tests, the bug can re-emerge any time someone refactors
    `delete_document` — and the failure mode is silent (works in unit
    tests with no linked Orders, blows up in production the moment a
    customer hits an order-linked document).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from domains.document import service as doc_service
from domains.document.models import Document
from domains.orders.models import Order


def _make_user_id(session_factory) -> int:
    """All tests share a single 'tester' user — the actual id is whatever
    auth_tokens seeded for us."""
    # We don't have an auth_tokens fixture in this file; build a user
    # directly. The CRUD path uses raw user_id, no role check.
    from domains.identity.models import User
    from infrastructure.security import hash_password

    db = session_factory()
    try:
        u = User(
            email="del_doc@example.com",
            hashed_password=hash_password("x"),
            full_name="Del",
            role="employee",
            is_active=True,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _make_doc(db: Session, user_id: int, **overrides) -> int:
    d = Document(
        user_id=user_id,
        filename=overrides.get("filename", "x.pdf"),
        file_url=overrides.get("file_url", "docs/x.pdf"),
        file_type=overrides.get("file_type", "pdf"),
        file_size_bytes=overrides.get("file_size_bytes", 100),
        doc_type=overrides.get("doc_type", "purchase_order"),
        status=overrides.get("status", "extracted"),
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d.id


def _make_order(db: Session, user_id: int, document_id: int | None) -> int:
    o = Order(
        user_id=user_id,
        filename="x.pdf",
        file_url="docs/x.pdf",
        file_type="pdf",
        status="ready",
        document_id=document_id,
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return o.id


@pytest.fixture
def user_id(session_factory) -> int:
    return _make_user_id(session_factory)


# ─── T1: no linked order, plain delete ────────────────────────


def test_delete_with_no_linked_order_succeeds(session_factory, user_id) -> None:
    db = session_factory()
    try:
        doc_id = _make_doc(db, user_id)
    finally:
        db.close()

    db = session_factory()
    try:
        result = doc_service.delete_document(
            db, document_id=doc_id, user_id=user_id, is_admin=False, force=False
        )
        assert result["ok"] is True
        assert result["unlinked_order_ids"] == []
        assert result["unlinked_order_id"] is None
        # Doc actually gone
        assert db.get(Document, doc_id) is None
    finally:
        db.close()


# ─── T2: linked order without force → 400 ─────────────────────


def test_delete_with_linked_order_no_force_raises(session_factory, user_id) -> None:
    db = session_factory()
    try:
        doc_id = _make_doc(db, user_id)
        order_id = _make_order(db, user_id, document_id=doc_id)
    finally:
        db.close()

    db = session_factory()
    try:
        with pytest.raises(doc_service.BadRequest) as exc_info:
            doc_service.delete_document(
                db,
                document_id=doc_id,
                user_id=user_id,
                is_admin=False,
                force=False,
            )
        assert str(order_id) in str(exc_info.value)
        # Doc + order MUST still exist after failed delete
        assert db.get(Document, doc_id) is not None
        assert db.get(Order, order_id) is not None
    finally:
        db.close()


# ─── T3: linked order with force → unlink + delete ────────────


def test_delete_with_linked_order_force_unlinks_and_preserves_order(
    session_factory, user_id
) -> None:
    db = session_factory()
    try:
        doc_id = _make_doc(db, user_id)
        order_id = _make_order(db, user_id, document_id=doc_id)
    finally:
        db.close()

    db = session_factory()
    try:
        result = doc_service.delete_document(
            db, document_id=doc_id, user_id=user_id, is_admin=False, force=True
        )
        assert result["ok"] is True
        assert result["unlinked_order_ids"] == [order_id]
        assert result["unlinked_order_id"] == order_id  # legacy field
        # Doc gone, order preserved with NULL document_id
        assert db.get(Document, doc_id) is None
        surviving = db.get(Order, order_id)
        assert surviving is not None
        assert surviving.document_id is None
    finally:
        db.close()


# ─── T4: storage cleanup failure is best-effort ───────────────


def test_delete_succeeds_even_when_storage_cleanup_fails(
    session_factory, user_id
) -> None:
    """Customer-visible expectation: DB delete commits even if GCS hiccups —
    storage cleanup is cosmetic, deletion is what they care about."""
    db = session_factory()
    try:
        doc_id = _make_doc(db, user_id, file_url="docs/will_fail.pdf")
    finally:
        db.close()

    class _BrokenStorage:
        def delete(self, _key: str) -> None:
            raise RuntimeError("simulated GCS outage")

    db = session_factory()
    try:
        with patch.object(doc_service, "get_storage", return_value=_BrokenStorage()):
            result = doc_service.delete_document(
                db,
                document_id=doc_id,
                user_id=user_id,
                is_admin=False,
                force=False,
            )
        assert result["ok"] is True
        # Despite storage error, the document is GONE from the DB.
        assert db.get(Document, doc_id) is None
    finally:
        db.close()


# ─── T5: multiple orders → all unlinked, all preserved ────────


def test_delete_with_multiple_linked_orders_force_unlinks_each(
    session_factory, user_id
) -> None:
    """Production reality: occasionally two Orders (re-extraction or v2->v3
    backfill) share the same source document. force=true must unlink ALL of
    them — partial unlink would leave the FK intact and crash again."""
    db = session_factory()
    try:
        doc_id = _make_doc(db, user_id)
        order_a = _make_order(db, user_id, document_id=doc_id)
        order_b = _make_order(db, user_id, document_id=doc_id)
        order_c = _make_order(db, user_id, document_id=doc_id)
    finally:
        db.close()

    db = session_factory()
    try:
        result = doc_service.delete_document(
            db, document_id=doc_id, user_id=user_id, is_admin=False, force=True
        )
        assert result["ok"] is True
        assert sorted(result["unlinked_order_ids"]) == sorted(
            [order_a, order_b, order_c]
        )
        assert db.get(Document, doc_id) is None
        for oid in (order_a, order_b, order_c):
            o = db.get(Order, oid)
            assert o is not None, f"order {oid} should survive"
            assert o.document_id is None, (
                f"order {oid} document_id must be NULL after unlink"
            )
    finally:
        db.close()
