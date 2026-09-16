"""DB access for the inquiry domain — SupplierTemplate (Phase 4) + Inquiry (Phase 5)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from domains.inquiry.models import Inquiry, InquirySupplier, SupplierTemplate

# ─── SupplierTemplate ─────────────────────────────────────────


def list_supplier_templates(
    db: Session, *, supplier_id: int | None = None
) -> list[SupplierTemplate]:
    stmt = select(SupplierTemplate).order_by(SupplierTemplate.id.desc())
    if supplier_id is not None:
        stmt = stmt.where(SupplierTemplate.supplier_id == supplier_id)
    return list(db.execute(stmt).scalars())


def get_supplier_template(db: Session, tpl_id: int) -> SupplierTemplate | None:
    return db.get(SupplierTemplate, tpl_id)


# ─── Inquiry / InquirySupplier ────────────────────────────────


def get_inquiry_by_order(db: Session, order_id: int) -> Inquiry | None:
    stmt = (
        select(Inquiry)
        .where(Inquiry.order_id == order_id)
        .order_by(Inquiry.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_single_inquiry_by_order(db: Session, order_id: int) -> Inquiry | None:
    """Return the legacy mutable single-order run, excluding group history."""
    stmt = (
        select(Inquiry)
        .where(Inquiry.order_id == order_id, Inquiry.group_id.is_(None))
        .order_by(Inquiry.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_latest_inquiry_by_group(db: Session, group_id: int) -> Inquiry | None:
    stmt = (
        select(Inquiry)
        .where(Inquiry.group_id == group_id)
        .order_by(Inquiry.version.desc(), Inquiry.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def list_inquiries_by_group(db: Session, group_id: int) -> list[Inquiry]:
    stmt = (
        select(Inquiry)
        .where(Inquiry.group_id == group_id)
        .order_by(Inquiry.version.desc(), Inquiry.id.desc())
    )
    return list(db.execute(stmt).scalars())


def get_inquiry(db: Session, inquiry_id: int) -> Inquiry | None:
    return db.get(Inquiry, inquiry_id)


def list_inquiry_suppliers(db: Session, inquiry_id: int) -> list[InquirySupplier]:
    stmt = (
        select(InquirySupplier)
        .where(InquirySupplier.inquiry_id == inquiry_id)
        .order_by(InquirySupplier.supplier_id.asc())
    )
    return list(db.execute(stmt).scalars())


def get_inquiry_supplier(db: Session, inquiry_id: int, supplier_id: int) -> InquirySupplier | None:
    stmt = select(InquirySupplier).where(
        InquirySupplier.inquiry_id == inquiry_id,
        InquirySupplier.supplier_id == supplier_id,
    )
    return db.execute(stmt).scalar_one_or_none()
