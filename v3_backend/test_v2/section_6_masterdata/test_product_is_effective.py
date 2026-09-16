"""Section 6 — Masterdata: Product.is_effective computed flag (v43+).

测试目标：
    `_serialize_product` 返回的 `is_effective` 是 UI 用来显示"有效/无效"
    badge 的字段，它等于 `status AND (effective_to is None OR
    effective_to >= today)`。这是 prod 2026-05-20 报告的 bug 修复——
    过期产品（effective_to 已过）原来还显示"有效"，UI 完全看不出。

    新字段是 *computed at read time*，DB 里 `status` 字段不变（仍是
    "管理员手动启用/停用"语义）。这两个字段的语义分离很关键：
      - `status` = 管理员显式开关
      - `is_effective` = 实际可用 = status AND (date in range)

为什么必须有：
    没有 cron / 触发器自动改 status；DB 里过期产品的 status 仍为 True，
    任何只读 status 的 UI 看到的都是"假有效"。读时算最简单也最实时——
    管理员一改 effective_to，下次查询立刻反映。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from domains.masterdata._products_service import _is_effective, serialize
from domains.masterdata.models import Product


def _new_product(
    *, status: bool, effective_to: datetime | None
) -> Product:
    """Build a minimal Product instance (not persisted) for _is_effective.

    We don't go through the DB / repo lookup_names path here because
    `_is_effective` only reads `status` + `effective_to` — keeping the
    fixture pure makes the assertion focus tight."""
    return Product(
        product_name_en="x",
        status=status,
        effective_to=effective_to,
    )


def test_future_effective_to_with_status_true_is_effective():
    """正常情况：手动启用 + 还没到期 → 有效。"""
    p = _new_product(
        status=True,
        effective_to=datetime.utcnow() + timedelta(days=30),
    )
    assert _is_effective(p) is True


def test_past_effective_to_with_status_true_is_not_effective():
    """**本次修复的 bug**：手动启用 + 已过期 → 无效。"""
    p = _new_product(
        status=True,
        effective_to=datetime.utcnow() - timedelta(days=1),
    )
    assert _is_effective(p) is False


def test_null_effective_to_with_status_true_is_effective():
    """无过期日 = 永远有效（没填 effective_to 视作不过期）。"""
    p = _new_product(status=True, effective_to=None)
    assert _is_effective(p) is True


def test_status_false_is_never_effective():
    """手动停用 → 不论日期都无效。"""
    for et in (
        None,
        datetime.utcnow() + timedelta(days=30),
        datetime.utcnow() - timedelta(days=30),
    ):
        p = _new_product(status=False, effective_to=et)
        assert _is_effective(p) is False, (
            f"status=False but is_effective=True with effective_to={et}"
        )


def test_effective_to_today_is_still_effective():
    """边界：effective_to = 今天 → 视作仍有效（包含当天）。"""
    today = datetime.combine(date.today(), time.min)
    p = _new_product(status=True, effective_to=today)
    assert _is_effective(p) is True


def test_serialize_includes_is_effective_field(db):
    """`serialize()` 返回的 dict 必须有 `is_effective` 字段——前端 contract。

    用真实 DB 路径而不是 mock，验证从 DB 拿出来的 Product 还能正确
    serialize（确认 datetime 类型 round-trip 没问题）。"""
    from domains.masterdata.models import Country, Category, Supplier, Product

    db.add(Country(name="JP", code="JP"))
    db.commit()
    country = db.query(Country).first()
    db.add(Category(name="Frozen"))
    db.commit()
    cat = db.query(Category).first()
    db.add(Supplier(name="S1"))
    db.commit()
    sup = db.query(Supplier).first()

    # Expired product
    expired = Product(
        product_name_en="Old Beef",
        code="OB-001",
        country_id=country.id,
        category_id=cat.id,
        supplier_id=sup.id,
        status=True,
        effective_to=datetime.utcnow() - timedelta(days=5),
    )
    # Still-good product
    fresh = Product(
        product_name_en="Fresh Beef",
        code="FB-001",
        country_id=country.id,
        category_id=cat.id,
        supplier_id=sup.id,
        status=True,
        effective_to=datetime.utcnow() + timedelta(days=30),
    )
    db.add_all([expired, fresh])
    db.commit()

    s_expired = serialize(db, expired)
    s_fresh = serialize(db, fresh)

    # The new field exists and reflects the truth
    assert "is_effective" in s_expired
    assert s_expired["is_effective"] is False, (
        "expired product must serialise as is_effective=False"
    )
    assert s_fresh["is_effective"] is True

    # The raw `status` field is unchanged (preserves "manual switch" semantics)
    assert s_expired["status"] is True
    assert s_fresh["status"] is True
