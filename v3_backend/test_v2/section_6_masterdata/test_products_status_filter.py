"""The legacy ``is_effective`` query parameter mirrors product status."""

from __future__ import annotations

from domains.masterdata import service as masterdata_service
from domains.masterdata.models import Category, Country, Product, Supplier


def _seed_basics(db) -> tuple[int, int, int]:
    """Add 1 country / 1 category / 1 supplier, return their ids."""
    db.add(Country(name="JP", code="JP"))
    db.add(Category(name="Frozen"))
    db.add(Supplier(name="S1"))
    db.commit()
    return (
        db.query(Country).first().id,
        db.query(Category).first().id,
        db.query(Supplier).first().id,
    )


def _make_product(
    db,
    *,
    code: str,
    status: bool,
    country_id: int,
    category_id: int,
    supplier_id: int,
) -> Product:
    p = Product(
        product_name_en=f"Product {code}",
        code=code,
        country_id=country_id,
        category_id=category_id,
        supplier_id=supplier_id,
        status=status,
    )
    db.add(p)
    db.commit()
    return p


def test_is_effective_true_returns_status_true_products(db) -> None:
    cid, catid, sid = _seed_basics(db)

    _make_product(db, code="ACTIVE", status=True,
                  country_id=cid, category_id=catid, supplier_id=sid)
    _make_product(db, code="EVERLASTING", status=True,
                  country_id=cid, category_id=catid, supplier_id=sid)
    _make_product(db, code="EXPIRED", status=True,
                  country_id=cid, category_id=catid, supplier_id=sid)
    _make_product(db, code="DISABLED", status=False,
                  country_id=cid, category_id=catid, supplier_id=sid)

    result = masterdata_service.list_products(db, is_effective=True)
    codes = {item["code"] for item in result["items"]}
    assert codes == {"ACTIVE", "EVERLASTING", "EXPIRED"}, (
        f"is_effective=True must include every status=true product; got {codes}"
    )
    # All returned items must indeed report is_effective=True in their serialised form
    for item in result["items"]:
        assert item["is_effective"] is True, (
            f"row {item['code']} returned by True-filter but has "
            f"is_effective={item['is_effective']!r} (SQL/Python divergence)"
        )


def test_is_effective_false_returns_status_false_products(db) -> None:
    cid, catid, sid = _seed_basics(db)

    _make_product(db, code="ACTIVE", status=True,
                  country_id=cid, category_id=catid, supplier_id=sid)
    _make_product(db, code="EXPIRED", status=True,
                  country_id=cid, category_id=catid, supplier_id=sid)
    _make_product(db, code="DISABLED", status=False,
                  country_id=cid, category_id=catid, supplier_id=sid)
    _make_product(db, code="BOTH", status=False,
                  country_id=cid, category_id=catid, supplier_id=sid)

    result = masterdata_service.list_products(db, is_effective=False)
    codes = {item["code"] for item in result["items"]}
    assert codes == {"DISABLED", "BOTH"}, (
        f"is_effective=False must include only status=false products; got {codes}"
    )
    for item in result["items"]:
        assert item["is_effective"] is False, (
            f"row {item['code']} returned by False-filter but has "
            f"is_effective={item['is_effective']!r} — SQL/Python disagree"
        )


def test_is_effective_omitted_returns_all(db) -> None:
    """Without the filter (None), pagination still works and ALL products
    are eligible — same behaviour as pre-v45."""
    cid, catid, sid = _seed_basics(db)
    _make_product(db, code="A1", status=True,
                  country_id=cid, category_id=catid, supplier_id=sid)
    _make_product(db, code="A2", status=False,
                  country_id=cid, category_id=catid, supplier_id=sid)
    _make_product(db, code="A3", status=True,
                  country_id=cid, category_id=catid, supplier_id=sid)

    result = masterdata_service.list_products(db)  # no is_effective
    codes = {item["code"] for item in result["items"]}
    assert codes == {"A1", "A2", "A3"}, (
        f"omitting is_effective must return everything; got {codes}"
    )


def test_status_true_filter_returns_enabled_product(db) -> None:
    cid, catid, sid = _seed_basics(db)
    _make_product(db, code="ENABLED", status=True,
                  country_id=cid, category_id=catid, supplier_id=sid)

    result = masterdata_service.list_products(db, is_effective=True)
    codes = {item["code"] for item in result["items"]}
    assert codes == {"ENABLED"}
