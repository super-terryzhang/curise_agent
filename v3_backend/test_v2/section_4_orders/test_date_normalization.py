"""Section 4 — Orders: PATCH date normalization (TD-3 2026-06-16).

测试目标：
    PATCH /api/orders/{id} 修改 `delivery_date` / `loading_date` /
    `order_date` 时，service 层把任意常见格式统一规范化为 YYYY-MM-DD
    再写库 —— 列还是 `String(50)`（TD-3 history：完整 Date 类型迁移
    被推后），但格式一致让字符串排序、字符串比较都能当日期用。

为什么重要：
    - LLM 提取走 Gemini schema 强制 YYYY-MM-DD，所以自动路径已经规范
    - 用户在详情页手工输 "6/15/2026" 或 "Jun 15, 2026" 也常见
    - 不规范化的话，按日期排序乱、合单按日期分组失败、SQL `WHERE
      >= '2026-06-01'` 漏掉非标准格式的行

设计方法：
    - `_normalize_patch_date` 是纯函数，直接 unit test 各输入
    - 整条 PATCH e2e 走 HTTP 验证落库的值是规范化后的
"""

from __future__ import annotations

import pytest

from domains.orders.models import Order
from domains.orders.service import _normalize_patch_date
from test_v2.fixtures.helpers import login, seed_user


# ─── _normalize_patch_date pure function ───────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-06-15", "2026-06-15"),  # already canonical → unchanged
        ("2026/06/15", "2026-06-15"),  # slash
        ("15/06/2026", "2026-06-15"),  # DMY slash
        ("06/15/2026", "2026-06-15"),  # MDY slash — overlaps with DMY but %m/%d/%Y handles unambiguously after %d/%m/%Y fails
        ("15-06-2026", "2026-06-15"),  # DMY dash
        ("15.06.2026", "2026-06-15"),  # DMY dot
        ("2026.06.15", "2026-06-15"),  # YMD dot
        ("Jun 15, 2026", "2026-06-15"),  # short month
        ("June 15, 2026", "2026-06-15"),  # long month
        ("  2026-06-15  ", "2026-06-15"),  # whitespace stripped
    ],
)
def test_normalize_patch_date_handles_common_formats(raw, expected):
    assert _normalize_patch_date(raw) == expected


def test_normalize_patch_date_passes_through_unparseable():
    """无法解析的字符串原样返回 —— 列是 String(50)，不应硬抛。"""
    assert _normalize_patch_date("not a date at all") == "not a date at all"


@pytest.mark.parametrize("v", [None, ""])
def test_normalize_patch_date_passes_through_empty(v):
    assert _normalize_patch_date(v) == v


# ─── PATCH e2e — value actually lands canonical in DB ──────────


def test_patch_normalizes_loading_date_into_yyyy_mm_dd_format(
    client, db, session_factory
):
    """整条链路：用户传非规范格式 → service 规范化 → 真实列存 YYYY-MM-DD。
    R6/R7 之后这条契约是"按日期排序/合单"正确的前提。"""
    seed_user(db, email="alice@x.test", role="employee")
    headers = login(client, "alice@x.test")
    # Minimal order seeded directly (we don't need a real PDF for this test).
    new_order = Order(
        user_id=1,
        filename="t.pdf",
        status="extracted",
    )
    db.add(new_order)
    db.commit()
    db.refresh(new_order)

    # User pastes a US-style date.
    r = client.patch(
        f"/api/orders/{new_order.id}",
        headers=headers,
        json={"loading_date": "June 15, 2026"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["loading_date"] == "2026-06-15"

    fresh = session_factory()
    try:
        o = fresh.get(Order, new_order.id)
        assert o.loading_date == "2026-06-15"
    finally:
        fresh.close()
