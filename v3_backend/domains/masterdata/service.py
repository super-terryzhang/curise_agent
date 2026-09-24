"""Master-data business logic — single entry point for HTTP, Agent, CLI.

This module hosts the smaller CRUD flows (Country, Category, Port, Supplier)
and re-exports the larger ones (Product, ExchangeRate) from sibling modules.

Callers should use the flat public API:

    from domains.masterdata import service
    service.create_country(db, body)
    service.create_product(db, body)
    service.fetch_exchange_rates(db, base=..., targets=...)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from domains.masterdata import repository as repo
from domains.masterdata._exchange_rates_service import (
    create_exchange_rate,
    delete_exchange_rate,
    fetch_exchange_rates,
    list_exchange_rates,
    update_exchange_rate,
)
from domains.masterdata._product_images_service import (
    add_product_image,
    batch_image_projections,
    delete_product_image,
    get_image_count,
    get_primary_thumbnail_url,
    list_product_images,
    reorder_product_images,
    update_product_image,
)
from domains.masterdata._products_service import (
    create_product,
    delete_product,
    list_image_upload_products,
    list_products,
    update_product,
)
from domains.masterdata._unit_conversion_service import (
    create_unit_conversion_rule,
    evaluate_unit_conversion,
    list_unit_conversion_rules,
    normalize_unit,
    product_pack_signature,
    retire_unit_conversion_rule,
    verify_unit_conversion_rule,
)
from domains.masterdata._validation import (
    assert_no_references,
    check_unique_code,
    require_country,
)
from domains.masterdata.errors import (
    BadRequest,
    Conflict,
    MasterdataError,
    NotFound,
    UpstreamUnavailable,
)
from domains.masterdata.models import (
    Category,
    Country,
    Port,
    Product,
    Supplier,
    SupplierCategory,
)
from domains.masterdata.price_periods import (
    attach_effective_prices as resolve_effective_product_prices,
)
from domains.masterdata.price_periods import (
    create_period as create_product_price_period,
)
from domains.masterdata.price_periods import (
    deactivate_period as deactivate_product_price_period,
)
from domains.masterdata.price_periods import (
    list_periods as list_product_price_periods,
)
from domains.masterdata.price_periods import (
    update_period as update_product_price_period,
)
from domains.masterdata.schemas import (
    CategoryCreate,
    CategoryUpdate,
    CountryCreate,
    CountryUpdate,
    PortCreate,
    PortUpdate,
    SupplierCreate,
    SupplierUpdate,
)

__all__ = [
    # Error hierarchy
    "MasterdataError",
    "NotFound",
    "Conflict",
    "BadRequest",
    "UpstreamUnavailable",
    # Countries
    "list_countries",
    "create_country",
    "update_country",
    "delete_country",
    # Categories
    "list_categories",
    "create_category",
    "update_category",
    "delete_category",
    # Ports
    "list_ports",
    "create_port",
    "update_port",
    "delete_port",
    # Suppliers
    "list_suppliers",
    "get_supplier",
    "create_supplier",
    "update_supplier",
    "delete_supplier",
    # Products (re-exported from _products_service)
    "list_products",
    "list_image_upload_products",
    "create_product",
    "update_product",
    "delete_product",
    "list_product_price_periods",
    "create_product_price_period",
    "update_product_price_period",
    "deactivate_product_price_period",
    "resolve_effective_product_prices",
    "add_product_image",
    "batch_image_projections",
    "delete_product_image",
    "get_image_count",
    "get_primary_thumbnail_url",
    "list_product_images",
    "reorder_product_images",
    "update_product_image",
    # Exchange rates (re-exported from _exchange_rates_service)
    "list_exchange_rates",
    "create_exchange_rate",
    "update_exchange_rate",
    "delete_exchange_rate",
    "fetch_exchange_rates",
    # Reusable unit conversion rules
    "normalize_unit",
    "product_pack_signature",
    "evaluate_unit_conversion",
    "list_unit_conversion_rules",
    "create_unit_conversion_rule",
    "verify_unit_conversion_rule",
    "retire_unit_conversion_rule",
]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ═══════════════════════════════════════════════════════════════
# Countries
# ═══════════════════════════════════════════════════════════════


def list_countries(db: Session) -> list[dict[str, Any]]:
    return [
        {"id": c.id, "name": c.name, "code": c.code, "status": c.status}
        for c in repo.list_countries(db)
    ]


def search_countries(
    db: Session, *, search: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """Countries with optional search + pagination. See search_suppliers for shape."""
    from sqlalchemy import func, select

    from domains.masterdata.models import Country

    base = select(Country)
    if search:
        base = base.where(Country.name.ilike(f"%{search}%"))
    total = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar() or 0
    bounded = max(1, min(int(limit) if limit else 50, 500))
    rows = list(db.execute(base.limit(bounded)).scalars())
    items = [
        {"id": c.id, "name": c.name, "code": c.code, "status": c.status}
        for c in rows
    ]
    return {"total": int(total), "items": items}


def create_country(db: Session, body: CountryCreate) -> dict[str, Any]:
    code = body.code.upper() if body.code else None
    check_unique_code(db, repo.get_country_by_code, code)
    obj = Country(name=body.name, code=code, status=body.status)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return {"id": obj.id, "name": obj.name, "code": obj.code, "status": obj.status}


def update_country(db: Session, country_id: int, body: CountryUpdate) -> dict[str, Any]:
    obj = repo.get_country(db, country_id)
    if obj is None:
        raise NotFound("国家不存在")
    data = body.model_dump(exclude_unset=True)
    if "code" in data and data["code"]:
        data["code"] = data["code"].upper()
        check_unique_code(db, repo.get_country_by_code, data["code"], exclude_id=country_id)
    for k, v in data.items():
        setattr(obj, k, v)
    obj.updated_at = _now()
    db.commit()
    db.refresh(obj)
    return {"id": obj.id, "name": obj.name, "code": obj.code, "status": obj.status}


def delete_country(db: Session, country_id: int) -> None:
    obj = repo.get_country(db, country_id)
    if obj is None:
        raise NotFound("国家不存在")
    assert_no_references(
        db,
        [
            (Port, "country_id", country_id, "港口"),
            (Supplier, "country_id", country_id, "供应商"),
            (Product, "country_id", country_id, "产品"),
        ],
    )
    db.delete(obj)
    db.commit()


# ═══════════════════════════════════════════════════════════════
# Categories
# ═══════════════════════════════════════════════════════════════


def search_categories(
    db: Session, *, search: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """Categories with optional search + pagination. See search_suppliers."""
    from sqlalchemy import func, select

    from domains.masterdata.models import Category

    base = select(Category)
    if search:
        base = base.where(Category.name.ilike(f"%{search}%"))
    total = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar() or 0
    bounded = max(1, min(int(limit) if limit else 50, 500))
    rows = list(db.execute(base.limit(bounded)).scalars())
    items = [
        {"id": c.id, "name": c.name, "code": c.code,
         "description": c.description, "status": c.status}
        for c in rows
    ]
    return {"total": int(total), "items": items}


def list_categories(db: Session) -> list[dict[str, Any]]:
    return [
        {
            "id": c.id,
            "name": c.name,
            "code": c.code,
            "description": c.description,
            "status": c.status,
        }
        for c in repo.list_categories(db)
    ]


def create_category(db: Session, body: CategoryCreate) -> dict[str, Any]:
    check_unique_code(db, repo.get_category_by_code, body.code)
    obj = Category(name=body.name, code=body.code, description=body.description, status=body.status)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return {
        "id": obj.id,
        "name": obj.name,
        "code": obj.code,
        "description": obj.description,
        "status": obj.status,
    }


def update_category(db: Session, category_id: int, body: CategoryUpdate) -> dict[str, Any]:
    obj = repo.get_category(db, category_id)
    if obj is None:
        raise NotFound("类别不存在")
    data = body.model_dump(exclude_unset=True)
    if "code" in data and data["code"]:
        check_unique_code(db, repo.get_category_by_code, data["code"], exclude_id=category_id)
    for k, v in data.items():
        setattr(obj, k, v)
    obj.updated_at = _now()
    db.commit()
    db.refresh(obj)
    return {
        "id": obj.id,
        "name": obj.name,
        "code": obj.code,
        "description": obj.description,
        "status": obj.status,
    }


def delete_category(db: Session, category_id: int) -> None:
    obj = repo.get_category(db, category_id)
    if obj is None:
        raise NotFound("类别不存在")
    assert_no_references(db, [(Product, "category_id", category_id, "产品")])
    refs = repo.count_rows_referencing(db, SupplierCategory, "category_id", category_id)
    if refs:
        raise Conflict(f"无法删除：有 {refs} 条供应商类别关联引用此记录")
    db.delete(obj)
    db.commit()


# ═══════════════════════════════════════════════════════════════
# Ports
# ═══════════════════════════════════════════════════════════════


def search_ports(
    db: Session, *, search: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """Ports with optional search + pagination."""
    from sqlalchemy import func, select

    from domains.masterdata.models import Port

    base = select(Port)
    if search:
        base = base.where(Port.name.ilike(f"%{search}%"))
    total = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar() or 0
    bounded = max(1, min(int(limit) if limit else 50, 500))
    rows = list(db.execute(base.limit(bounded)).scalars())
    countries = {c.id: c.name for c in repo.list_countries(db)}
    items = [
        {
            "id": p.id, "name": p.name, "code": p.code,
            "country_id": p.country_id,
            "country_name": countries.get(p.country_id) if p.country_id else None,
            "location": p.location,
        }
        for p in rows
    ]
    return {"total": int(total), "items": items}


def list_ports(db: Session) -> list[dict[str, Any]]:
    countries = {c.id: c.name for c in repo.list_countries(db)}
    return [
        {
            "id": p.id,
            "name": p.name,
            "code": p.code,
            "country_id": p.country_id,
            "country_name": countries.get(p.country_id) if p.country_id else None,
            "location": p.location,
            "status": p.status,
        }
        for p in repo.list_ports(db)
    ]


def create_port(db: Session, body: PortCreate) -> dict[str, Any]:
    check_unique_code(db, repo.get_port_by_code, body.code)
    require_country(db, body.country_id)
    obj = Port(
        name=body.name,
        code=body.code,
        country_id=body.country_id,
        location=body.location,
        status=body.status,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return _serialize_port(db, obj)


def update_port(db: Session, port_id: int, body: PortUpdate) -> dict[str, Any]:
    obj = repo.get_port(db, port_id)
    if obj is None:
        raise NotFound("港口不存在")
    data = body.model_dump(exclude_unset=True)
    if "code" in data and data["code"]:
        check_unique_code(db, repo.get_port_by_code, data["code"], exclude_id=port_id)
    if "country_id" in data:
        require_country(db, data["country_id"])
    for k, v in data.items():
        setattr(obj, k, v)
    obj.updated_at = _now()
    db.commit()
    db.refresh(obj)
    return _serialize_port(db, obj)


def delete_port(db: Session, port_id: int) -> None:
    obj = repo.get_port(db, port_id)
    if obj is None:
        raise NotFound("港口不存在")
    assert_no_references(db, [(Product, "port_id", port_id, "产品")])
    db.delete(obj)
    db.commit()


def get_port(db: Session, port_id: int) -> dict[str, Any] | None:
    """Public lookup for cross-domain callers — return port as dict, or None.

    Added 2026-06-16 (TD-1) so the inquiry domain can read port info
    without importing `Port` directly.
    """
    p = repo.get_port(db, port_id)
    return _serialize_port(db, p) if p else None


def _serialize_port(db: Session, p: Port) -> dict[str, Any]:
    country = repo.get_country(db, p.country_id) if p.country_id else None
    return {
        "id": p.id,
        "name": p.name,
        "code": p.code,
        "country_id": p.country_id,
        "country_name": country.name if country else None,
        "location": p.location,
        "status": p.status,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


# ═══════════════════════════════════════════════════════════════
# Suppliers
# ═══════════════════════════════════════════════════════════════


def list_suppliers(db: Session) -> list[dict[str, Any]]:
    # Inline serialization (not _serialize_supplier) so we batch country +
    # category lookups instead of N+1'ing them per supplier. Must include
    # all 9 letterhead fields — frontend's SupplierItem type + the inquiry
    # card's letterhead status both read this shape.
    countries = {c.id: c.name for c in repo.list_countries(db)}
    cat_map = repo.get_supplier_categories_map(db)
    return [
        {
            "id": s.id,
            "name": s.name,
            "country_id": s.country_id,
            "country_name": countries.get(s.country_id) if s.country_id else None,
            "contact": s.contact,
            "email": s.email,
            "phone": s.phone,
            "address": s.address,
            "zip_code": s.zip_code,
            "fax": s.fax,
            "default_payment_method": s.default_payment_method,
            "default_payment_terms": s.default_payment_terms,
            "status": s.status,
            "categories": [name for _, name in cat_map.get(s.id, [])],
            "category_ids": [cid for cid, _ in cat_map.get(s.id, [])],
        }
        for s in repo.list_suppliers(db)
    ]


def search_suppliers(
    db: Session, *, search: str | None = None, limit: int = 20
) -> dict[str, Any]:
    """List suppliers with optional search + pagination, returning a true
    COUNT(*). Use this from agent tools to avoid hand-rolling SQL and to
    correctly answer "how many suppliers" questions.

    Returned shape: `{"total": int, "items": [...]}` — `total` is the DB
    count matching the filter, NOT len(items).
    """
    from sqlalchemy import func, select

    from domains.masterdata.models import Supplier

    base = select(Supplier)
    if search:
        base = base.where(Supplier.name.ilike(f"%{search}%"))
    total = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar() or 0
    bounded = max(1, min(int(limit) if limit else 20, 200))
    rows = list(db.execute(base.limit(bounded)).scalars())

    countries = {c.id: c.name for c in repo.list_countries(db)}
    cat_map = repo.get_supplier_categories_map(db)
    items = [
        {
            "id": s.id,
            "name": s.name,
            "country_id": s.country_id,
            "country_name": countries.get(s.country_id) if s.country_id else None,
            "contact": s.contact,
            "email": s.email,
            "phone": s.phone,
            "address": s.address,
            "zip_code": s.zip_code,
            "fax": s.fax,
            "default_payment_method": s.default_payment_method,
            "default_payment_terms": s.default_payment_terms,
            "status": s.status,
            "categories": [name for _, name in cat_map.get(s.id, [])],
            "category_ids": [cid for cid, _ in cat_map.get(s.id, [])],
        }
        for s in rows
    ]
    return {"total": int(total), "items": items}


def create_supplier(db: Session, body: SupplierCreate) -> dict[str, Any]:
    require_country(db, body.country_id)
    obj = Supplier(
        name=body.name,
        country_id=body.country_id,
        contact=body.contact,
        email=body.email,
        phone=body.phone,
        address=body.address,
        zip_code=body.zip_code,
        fax=body.fax,
        default_payment_method=body.default_payment_method,
        default_payment_terms=body.default_payment_terms,
        status=body.status,
    )
    db.add(obj)
    db.flush()
    if body.category_ids:
        repo.replace_supplier_categories(db, obj.id, body.category_ids)
    db.commit()
    db.refresh(obj)
    return _serialize_supplier(db, obj)


def get_supplier(db: Session, supplier_id: int) -> dict[str, Any] | None:
    """Fetch one supplier as a serialized dict, or None if missing.

    Used by the inquiry orchestrator to load supplier contact info for the
    Excel header — kept public so cross-domain callers don't reach into
    the masterdata repository directly.
    """
    obj = repo.get_supplier(db, supplier_id)
    if obj is None:
        return None
    return _serialize_supplier(db, obj)


def update_supplier(db: Session, supplier_id: int, body: SupplierUpdate) -> dict[str, Any]:
    obj = repo.get_supplier(db, supplier_id)
    if obj is None:
        raise NotFound("供应商不存在")
    data = body.model_dump(exclude_unset=True)
    category_ids = data.pop("category_ids", None)
    if "country_id" in data:
        require_country(db, data["country_id"])
    for k, v in data.items():
        setattr(obj, k, v)
    obj.updated_at = _now()
    if category_ids is not None:
        repo.replace_supplier_categories(db, supplier_id, category_ids)
    db.commit()
    db.refresh(obj)
    return _serialize_supplier(db, obj)


def delete_supplier(db: Session, supplier_id: int) -> None:
    obj = repo.get_supplier(db, supplier_id)
    if obj is None:
        raise NotFound("供应商不存在")
    assert_no_references(db, [(Product, "supplier_id", supplier_id, "产品")])
    db.query(SupplierCategory).filter(SupplierCategory.supplier_id == supplier_id).delete()
    db.delete(obj)
    db.commit()


def _serialize_supplier(db: Session, s: Supplier) -> dict[str, Any]:
    country = repo.get_country(db, s.country_id) if s.country_id else None
    cat_pairs = repo.get_supplier_categories_map(db).get(s.id, [])
    return {
        "id": s.id,
        "name": s.name,
        "country_id": s.country_id,
        "country_name": country.name if country else None,
        "contact": s.contact,
        "email": s.email,
        "phone": s.phone,
        # Inquiry letterhead fields — see schemas.SupplierCreate for the
        # explanation. Returned even when NULL so the frontend can render
        # the empty state consistently.
        "address": s.address,
        "zip_code": s.zip_code,
        "fax": s.fax,
        "default_payment_method": s.default_payment_method,
        "default_payment_terms": s.default_payment_terms,
        "categories": [name for _, name in cat_pairs],
        "category_ids": [cid for cid, _ in cat_pairs],
        "status": s.status,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }
