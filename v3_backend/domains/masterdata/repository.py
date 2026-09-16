"""DB access layer for master data.

SQL queries live here; service layer never calls `db.query(...)` directly.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from domains.masterdata.models import (
    Category,
    Country,
    ExchangeRate,
    Port,
    Product,
    Supplier,
    SupplierCategory,
)

# ─── Country ─────────────────────────────────────────────────


def list_countries(db: Session) -> list[Country]:
    return list(db.execute(select(Country).order_by(Country.name)).scalars())


def get_country(db: Session, country_id: int) -> Country | None:
    return db.get(Country, country_id)


def get_country_by_code(db: Session, code: str) -> Country | None:
    return db.execute(select(Country).where(Country.code == code)).scalar_one_or_none()


def country_exists(db: Session, country_id: int) -> bool:
    return db.get(Country, country_id) is not None


# ─── Category ────────────────────────────────────────────────


def list_categories(db: Session) -> list[Category]:
    return list(db.execute(select(Category).order_by(Category.name)).scalars())


def get_category(db: Session, category_id: int) -> Category | None:
    return db.get(Category, category_id)


def get_category_by_code(db: Session, code: str) -> Category | None:
    return db.execute(select(Category).where(Category.code == code)).scalar_one_or_none()


def category_exists(db: Session, category_id: int) -> bool:
    return db.get(Category, category_id) is not None


# ─── Port ────────────────────────────────────────────────────


def list_ports(db: Session) -> list[Port]:
    return list(db.execute(select(Port).order_by(Port.name)).scalars())


def get_port(db: Session, port_id: int) -> Port | None:
    return db.get(Port, port_id)


def get_port_by_code(db: Session, code: str) -> Port | None:
    return db.execute(select(Port).where(Port.code == code)).scalar_one_or_none()


def port_exists(db: Session, port_id: int) -> bool:
    return db.get(Port, port_id) is not None


# ─── Supplier ────────────────────────────────────────────────


def list_suppliers(db: Session) -> list[Supplier]:
    return list(db.execute(select(Supplier).order_by(Supplier.name)).scalars())


def get_supplier(db: Session, supplier_id: int) -> Supplier | None:
    return db.get(Supplier, supplier_id)


def supplier_exists(db: Session, supplier_id: int) -> bool:
    return db.get(Supplier, supplier_id) is not None


def get_supplier_category_ids(db: Session, supplier_id: int) -> list[int]:
    rows = db.execute(
        select(SupplierCategory.category_id)
        .where(SupplierCategory.supplier_id == supplier_id)
        .order_by(SupplierCategory.category_id)
    ).all()
    return [r[0] for r in rows]


def get_supplier_categories_map(db: Session) -> dict[int, list[tuple[int, str]]]:
    """For every supplier, return its [(category_id, category_name), ...] list."""
    rows = db.execute(
        select(SupplierCategory.supplier_id, Category.id, Category.name)
        .join(Category, Category.id == SupplierCategory.category_id)
        .order_by(Category.name)
    ).all()
    out: dict[int, list[tuple[int, str]]] = {}
    for sid, cid, cname in rows:
        out.setdefault(sid, []).append((cid, cname))
    return out


def replace_supplier_categories(db: Session, supplier_id: int, category_ids: list[int]) -> None:
    """Replace the supplier's category assignments. Skips unknown category_ids."""
    db.query(SupplierCategory).filter(SupplierCategory.supplier_id == supplier_id).delete()
    for cid in category_ids:
        if category_exists(db, cid):
            db.add(SupplierCategory(supplier_id=supplier_id, category_id=cid))


# ─── Product ─────────────────────────────────────────────────


def list_products(
    db: Session,
    *,
    search: str | None = None,
    country_id: int | None = None,
    category_id: int | None = None,
    supplier_id: int | None = None,
    is_effective: bool | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[int, list[Product]]:
    """List products with filters + pagination.

    `is_effective` matches the same definition as
    `_products_service._is_effective` so the filter result agrees with
    what the StatusBadge would show: True means `status AND (effective_to
    is NULL OR effective_to >= today)`; False means the negation. If both
    Python-side compute and this SQL clause diverge, the filter would
    show rows that the UI badges differently — keep them in lockstep.
    """
    from datetime import date as _date

    stmt = select(Product)
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where((Product.product_name_en.ilike(pattern)) | (Product.code.ilike(pattern)))
    if country_id is not None:
        stmt = stmt.where(Product.country_id == country_id)
    if category_id is not None:
        stmt = stmt.where(Product.category_id == category_id)
    if supplier_id is not None:
        stmt = stmt.where(Product.supplier_id == supplier_id)
    if is_effective is not None:
        today = _date.today()
        effective_predicate = (
            (Product.status.is_(True))
            & (
                (Product.effective_to.is_(None))
                | (Product.effective_to >= today)
            )
        )
        stmt = stmt.where(effective_predicate if is_effective else ~effective_predicate)

    count_stmt = stmt.with_only_columns(Product.id).order_by(None)
    total = len(db.execute(count_stmt).all())

    items = list(db.execute(stmt.order_by(Product.id.desc()).limit(limit).offset(offset)).scalars())
    return total, items


def get_product(db: Session, product_id: int) -> Product | None:
    return db.get(Product, product_id)


def lookup_names(
    db: Session,
    *,
    country_id: int | None = None,
    category_id: int | None = None,
    supplier_id: int | None = None,
    port_id: int | None = None,
) -> dict[str, str | None]:
    """Single-pass lookup of the four parent names for a Product row."""
    out: dict[str, str | None] = {
        "country_name": None,
        "category_name": None,
        "supplier_name": None,
        "port_name": None,
    }
    if country_id:
        c = get_country(db, country_id)
        out["country_name"] = c.name if c else None
    if category_id:
        cat = get_category(db, category_id)
        out["category_name"] = cat.name if cat else None
    if supplier_id:
        s = get_supplier(db, supplier_id)
        out["supplier_name"] = s.name if s else None
    if port_id:
        p = get_port(db, port_id)
        out["port_name"] = p.name if p else None
    return out


# ─── Reference counts (used by delete validation) ────────────


def count_rows_referencing(db: Session, table: Any, column: str, value: int) -> int:
    stmt = select(table).where(getattr(table, column) == value)
    return len(db.execute(stmt).all())


# ─── Exchange rate ───────────────────────────────────────────


def list_exchange_rates(
    db: Session,
    *,
    from_currency: str | None = None,
    to_currency: str | None = None,
) -> list[ExchangeRate]:
    stmt = select(ExchangeRate)
    if from_currency:
        stmt = stmt.where(ExchangeRate.from_currency == from_currency.upper())
    if to_currency:
        stmt = stmt.where(ExchangeRate.to_currency == to_currency.upper())
    return list(
        db.execute(
            stmt.order_by(
                ExchangeRate.from_currency,
                ExchangeRate.to_currency,
                ExchangeRate.effective_date.desc(),
            )
        ).scalars()
    )


def get_exchange_rate_row(db: Session, rate_id: int) -> ExchangeRate | None:
    return db.get(ExchangeRate, rate_id)


def find_rate_on_date(
    db: Session,
    from_currency: str,
    to_currency: str,
    effective_date: date,
) -> ExchangeRate | None:
    return db.execute(
        select(ExchangeRate).where(
            ExchangeRate.from_currency == from_currency,
            ExchangeRate.to_currency == to_currency,
            ExchangeRate.effective_date == effective_date,
        )
    ).scalar_one_or_none()


def find_latest_rate_on_or_before(
    db: Session,
    from_currency: str,
    to_currency: str,
    on_or_before: date,
) -> ExchangeRate | None:
    return db.execute(
        select(ExchangeRate)
        .where(
            ExchangeRate.from_currency == from_currency,
            ExchangeRate.to_currency == to_currency,
            ExchangeRate.effective_date <= on_or_before,
        )
        .order_by(ExchangeRate.effective_date.desc())
        .limit(1)
    ).scalar_one_or_none()
