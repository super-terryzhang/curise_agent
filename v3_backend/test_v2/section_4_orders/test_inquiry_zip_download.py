"""Section 4 — orders: GET /api/orders/{id}/inquiry-files.zip contract.

测试目标：
    Pin the ZIP-bundled inquiry download endpoint (introduced 2026-05-22).
    Customer-facing bug: clicking "全部下载" in the inquiry tab only
    downloaded the first supplier file because Chrome silently blocks
    sites from triggering >1 automatic download in a row.
    The fix is a server-streamed ZIP — one click → one network call →
    one file save → no per-browser policy guesswork.

为什么必须有：
    Without this test, future refactors (e.g. switching storage backend,
    or moving the inquiry repo) can silently break the customer's
    primary file-fetch path with no signal until production complains.
"""

from __future__ import annotations

import io
import zipfile

from sqlalchemy.orm import Session

from domains.inquiry.models import Inquiry, InquirySupplier
from domains.orders.models import Order
from infrastructure.storage import set_storage_for_tests


class _MemStorage:
    """In-memory storage stub — bytes keyed by storage_key."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self._files = files

    def upload(self, storage_key: str, content: bytes, content_type: str | None = None) -> str:
        self._files[storage_key] = content
        return storage_key

    def download(self, storage_key: str) -> bytes:
        return self._files[storage_key]

    def delete(self, storage_key: str) -> None:
        self._files.pop(storage_key, None)

    def get_signed_url(self, storage_key: str, expires_in: int = 3600) -> str:
        return f"mem://{storage_key}"


def _seed_order_with_two_inquiry_files(db: Session, user_id: int) -> int:
    """Create one Order + Inquiry + 2 InquirySupplier rows, return order_id.

    Files live in the in-memory storage stub registered by the caller.
    """
    order = Order(
        user_id=user_id,
        filename="test.pdf",
        file_url="orders/test.pdf",
        file_type="pdf",
        status="ready",
        po_number="PO_TEST_001",
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    inquiry = Inquiry(order_id=order.id, status="completed", supplier_count=2)
    db.add(inquiry)
    db.commit()
    db.refresh(inquiry)

    for sid, name, url in [
        (1, "供应商A", "inquiries/inquiry_a.xlsx"),
        (2, "供应商B", "inquiries/inquiry_b.xlsx"),
    ]:
        db.add(
            InquirySupplier(
                inquiry_id=inquiry.id,
                supplier_id=sid,
                supplier_name=name,
                status="completed",
                excel_file_url=url,
            )
        )
    db.commit()
    return order.id


def _auth_header(auth_tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_tokens['access_token']}"}


def test_zip_download_bundles_all_inquiry_files(client, session_factory, auth_tokens) -> None:
    """Happy path: 2 supplier files → 1 ZIP containing both, valid + readable."""
    storage = _MemStorage(
        {
            "orders/test.pdf": b"%PDF-1.4 fake",
            "inquiries/inquiry_a.xlsx": b"supplier-a-bytes",
            "inquiries/inquiry_b.xlsx": b"supplier-b-bytes-longer",
        }
    )
    set_storage_for_tests(storage)
    try:
        db = session_factory()
        try:
            user_id = auth_tokens["user"]["id"]
            order_id = _seed_order_with_two_inquiry_files(db, user_id)
        finally:
            db.close()

        res = client.get(
            f"/api/orders/{order_id}/inquiry-files.zip",
            headers=_auth_header(auth_tokens),
        )
        assert res.status_code == 200, res.text
        assert res.headers["content-type"] == "application/zip"
        assert "PO_TEST_001_inquiries.zip" in res.headers["content-disposition"]

        zf = zipfile.ZipFile(io.BytesIO(res.content))
        names = sorted(zf.namelist())
        assert names == ["inquiry_a.xlsx", "inquiry_b.xlsx"], (
            f"expected both inquiry basenames in ZIP, got {names}"
        )
        assert zf.read("inquiry_a.xlsx") == b"supplier-a-bytes"
        assert zf.read("inquiry_b.xlsx") == b"supplier-b-bytes-longer"
    finally:
        set_storage_for_tests(None)


def test_zip_download_404_when_no_inquiry(client, session_factory, auth_tokens) -> None:
    """Order exists but has no Inquiry row → 404, not 500."""
    set_storage_for_tests(_MemStorage({}))
    try:
        db = session_factory()
        try:
            user_id = auth_tokens["user"]["id"]
            order = Order(
                user_id=user_id,
                filename="x.pdf",
                file_url="orders/x.pdf",
                file_type="pdf",
                status="ready",
                po_number="PO_NO_INQUIRY",
            )
            db.add(order)
            db.commit()
            db.refresh(order)
            order_id = order.id
        finally:
            db.close()

        res = client.get(
            f"/api/orders/{order_id}/inquiry-files.zip",
            headers=_auth_header(auth_tokens),
        )
        assert res.status_code == 404, res.text
    finally:
        set_storage_for_tests(None)


def test_zip_download_skips_suppliers_without_excel_url(
    client, session_factory, auth_tokens
) -> None:
    """Some suppliers may still be generating (no excel_file_url yet) — skip
    them silently rather than 500. Only completed files end up in the ZIP."""
    storage = _MemStorage(
        {
            "orders/p.pdf": b"%PDF",
            "inquiries/done.xlsx": b"done-bytes",
        }
    )
    set_storage_for_tests(storage)
    try:
        db = session_factory()
        try:
            user_id = auth_tokens["user"]["id"]
            order = Order(
                user_id=user_id,
                filename="p.pdf",
                file_url="orders/p.pdf",
                file_type="pdf",
                status="ready",
                po_number="PO_PARTIAL",
            )
            db.add(order)
            db.commit()
            db.refresh(order)

            inq = Inquiry(order_id=order.id, status="in_progress", supplier_count=2)
            db.add(inq)
            db.commit()
            db.refresh(inq)

            db.add(
                InquirySupplier(
                    inquiry_id=inq.id,
                    supplier_id=1,
                    supplier_name="finished",
                    status="completed",
                    excel_file_url="inquiries/done.xlsx",
                )
            )
            db.add(
                InquirySupplier(
                    inquiry_id=inq.id,
                    supplier_id=2,
                    supplier_name="still-running",
                    status="generating",
                    excel_file_url=None,
                )
            )
            db.commit()
            order_id = order.id
        finally:
            db.close()

        res = client.get(
            f"/api/orders/{order_id}/inquiry-files.zip",
            headers=_auth_header(auth_tokens),
        )
        assert res.status_code == 200, res.text
        zf = zipfile.ZipFile(io.BytesIO(res.content))
        assert zf.namelist() == ["done.xlsx"]
    finally:
        set_storage_for_tests(None)


def test_zip_download_404_when_all_suppliers_pending(
    client, session_factory, auth_tokens
) -> None:
    """If every supplier is still generating, ZIP would be empty — return 404
    rather than handing the user a zero-file zip they'll be confused by."""
    set_storage_for_tests(_MemStorage({}))
    try:
        db = session_factory()
        try:
            user_id = auth_tokens["user"]["id"]
            order = Order(
                user_id=user_id,
                filename="q.pdf",
                file_url="orders/q.pdf",
                file_type="pdf",
                status="ready",
                po_number="PO_ALL_PENDING",
            )
            db.add(order)
            db.commit()
            db.refresh(order)

            inq = Inquiry(order_id=order.id, status="in_progress", supplier_count=1)
            db.add(inq)
            db.commit()
            db.refresh(inq)

            db.add(
                InquirySupplier(
                    inquiry_id=inq.id,
                    supplier_id=1,
                    supplier_name="pending",
                    status="pending",
                    excel_file_url=None,
                )
            )
            db.commit()
            order_id = order.id
        finally:
            db.close()

        res = client.get(
            f"/api/orders/{order_id}/inquiry-files.zip",
            headers=_auth_header(auth_tokens),
        )
        assert res.status_code == 404, res.text
    finally:
        set_storage_for_tests(None)
