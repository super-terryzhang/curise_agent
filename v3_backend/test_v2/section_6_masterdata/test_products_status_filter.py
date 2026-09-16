"""Section 6 — Masterdata: Product list `is_effective` filter (v45+).

测试目标：
    `GET /api/data/products?is_effective=true|false` 返回的 product 列表
    必须和前端 StatusBadge 显示的"有效/无效"完全一致。两边的判定逻辑
    分别是：
      - Python: `_products_service._is_effective(p, today)`
      - SQL:    `Product.status & (effective_to IS NULL | effective_to >= today)`
    它们应该等价 —— 这条测试每个组合都覆盖，防止两边漂移。

为什么重要：
    用户在前端 Products tab 点"有效"过滤想看真正能用的产品，如果 SQL
    判定和 badge 判定不一致，用户会看到"列表说有效，badge 显示无效"
    这种自我矛盾的 UI。这条测试 pin 住两者必须一致。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

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
    effective_to: datetime | None,
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
        effective_to=effective_to,
    )
    db.add(p)
    db.commit()
    return p


def test_is_effective_true_returns_only_active_unexpired(db) -> None:
    """`is_effective=True` returns: manual-on AND (no expiry OR future expiry).

    Verifies the SQL filter agrees with what `_is_effective` would compute
    in Python — they are duplicated logic across two languages, so the
    test must exercise both endpoints."""
    cid, catid, sid = _seed_basics(db)
    now = datetime.utcnow()

    _make_product(db, code="ACTIVE", status=True,
                  effective_to=now + timedelta(days=30),
                  country_id=cid, category_id=catid, supplier_id=sid)
    _make_product(db, code="EVERLASTING", status=True, effective_to=None,
                  country_id=cid, category_id=catid, supplier_id=sid)
    _make_product(db, code="EXPIRED", status=True,
                  effective_to=now - timedelta(days=1),
                  country_id=cid, category_id=catid, supplier_id=sid)
    _make_product(db, code="DISABLED", status=False,
                  effective_to=now + timedelta(days=30),
                  country_id=cid, category_id=catid, supplier_id=sid)

    result = masterdata_service.list_products(db, is_effective=True)
    codes = {item["code"] for item in result["items"]}
    assert codes == {"ACTIVE", "EVERLASTING"}, (
        f"is_effective=True must include only manual-on + unexpired; got {codes}"
    )
    # All returned items must indeed report is_effective=True in their serialised form
    for item in result["items"]:
        assert item["is_effective"] is True, (
            f"row {item['code']} returned by True-filter but has "
            f"is_effective={item['is_effective']!r} (SQL/Python divergence)"
        )


def test_is_effective_false_returns_disabled_or_expired(db) -> None:
    cid, catid, sid = _seed_basics(db)
    now = datetime.utcnow()

    _make_product(db, code="ACTIVE", status=True,
                  effective_to=now + timedelta(days=30),
                  country_id=cid, category_id=catid, supplier_id=sid)
    _make_product(db, code="EXPIRED", status=True,
                  effective_to=now - timedelta(days=1),
                  country_id=cid, category_id=catid, supplier_id=sid)
    _make_product(db, code="DISABLED", status=False, effective_to=None,
                  country_id=cid, category_id=catid, supplier_id=sid)
    _make_product(db, code="BOTH", status=False,
                  effective_to=now - timedelta(days=5),
                  country_id=cid, category_id=catid, supplier_id=sid)

    result = masterdata_service.list_products(db, is_effective=False)
    codes = {item["code"] for item in result["items"]}
    assert codes == {"EXPIRED", "DISABLED", "BOTH"}, (
        f"is_effective=False must include disabled + expired; got {codes}"
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
    now = datetime.utcnow()
    _make_product(db, code="A1", status=True, effective_to=None,
                  country_id=cid, category_id=catid, supplier_id=sid)
    _make_product(db, code="A2", status=False, effective_to=None,
                  country_id=cid, category_id=catid, supplier_id=sid)
    _make_product(db, code="A3", status=True,
                  effective_to=now - timedelta(days=10),
                  country_id=cid, category_id=catid, supplier_id=sid)

    result = masterdata_service.list_products(db)  # no is_effective
    codes = {item["code"] for item in result["items"]}
    assert codes == {"A1", "A2", "A3"}, (
        f"omitting is_effective must return everything; got {codes}"
    )


def test_is_effective_today_boundary_is_inclusive(db) -> None:
    """Edge: `effective_to == today` is still effective (we use `>=` not
    `>`). Mirrors the `_is_effective` Python helper boundary semantics."""
    cid, catid, sid = _seed_basics(db)
    today_start = datetime.combine(date.today(), time.min)

    _make_product(db, code="EXPIRES_TODAY", status=True,
                  effective_to=today_start,
                  country_id=cid, category_id=catid, supplier_id=sid)

    result = masterdata_service.list_products(db, is_effective=True)
    codes = {item["code"] for item in result["items"]}
    assert "EXPIRES_TODAY" in codes, (
        "effective_to=today should still be considered effective (>=)"
    )
