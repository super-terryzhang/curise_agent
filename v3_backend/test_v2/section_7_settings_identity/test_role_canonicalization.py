"""Section 7 — Identity: role-alias canonicalization (TD-4 2026-06-16).

测试目标：
    `super_admin` / `user` 两个老别名必须在写入边界（create_user / update_user）
    被映射到 canonical 名（`superadmin` / `employee`），DB 永远只存规范值。

为什么重要：
    两个字符串指同一个角色是潜在权限漏洞：
    - `if user.role == "superadmin"` 会漏掉 `super_admin` 用户 → 越权
    - 列表统计按 role 分组会重复
    - 检查 `require_role("superadmin")` 行为不一致

设计方法：
    - `canonicalize_role()` 是纯函数 — 直接断言映射
    - `create_user` / `update_user` 走 service 层 — 真实 DB 写入后回读
    - `ROLE_LEVELS` 字典里不该有别名键 — 锁死契约
"""

from __future__ import annotations

import pytest

from domains.identity import service as identity_service
from infrastructure.security import ROLE_LEVELS, canonicalize_role


# ─── canonicalize_role pure function ──────────────────────────


@pytest.mark.parametrize(
    "alias,canonical",
    [
        ("super_admin", "superadmin"),
        ("user", "employee"),
        # canonical values pass through unchanged
        ("superadmin", "superadmin"),
        ("admin", "admin"),
        ("finance", "finance"),
        ("employee", "employee"),
    ],
)
def test_canonicalize_role_maps_alias_to_canonical(alias, canonical):
    """每个别名都映射到其规范名；canonical 名字 round-trip 不变。"""
    assert canonicalize_role(alias) == canonical


def test_canonicalize_role_leaves_unknown_role_unchanged():
    """未知 role 不映射 —— 让上游 validation 决定接受 / 拒绝。"""
    assert canonicalize_role("unknown_role") == "unknown_role"


# ─── ROLE_LEVELS contract ─────────────────────────────────────


def test_role_levels_no_longer_contains_aliases():
    """ROLE_LEVELS 不能含别名 —— 否则下次有人复用旧字符串会重新打开
    'super_admin' / 'user' 这两个洞。锁死。"""
    assert "super_admin" not in ROLE_LEVELS
    assert "user" not in ROLE_LEVELS


def test_role_levels_contains_only_4_canonical_roles():
    """系统只承认 4 个 canonical role。"""
    assert set(ROLE_LEVELS.keys()) == {"superadmin", "admin", "finance", "employee"}


# ─── Service layer normalization ──────────────────────────────


def test_create_user_normalizes_super_admin_alias_to_superadmin(db):
    """通过 service 层用别名建用户 —— DB 行应该存的是 canonical。"""
    user = identity_service.create_user(
        db,
        email="su@x.test",
        full_name="Su",
        password="TemporaryPass123!",
        role="super_admin",  # alias
    )
    assert user.role == "superadmin"


def test_create_user_normalizes_user_alias_to_employee(db):
    user = identity_service.create_user(
        db,
        email="u@x.test",
        full_name="U",
        password="TemporaryPass123!",
        role="user",  # alias
    )
    assert user.role == "employee"


def test_update_user_normalizes_alias_on_role_change(db):
    """改 role 时如果客户端还在传别名 —— 也要归一化。"""
    user = identity_service.create_user(
        db,
        email="alice@x.test",
        full_name="A",
        password="TemporaryPass123!",
        role="employee",
    )
    updated = identity_service.update_user(
        db, user_id=user.id, role="super_admin"
    )
    assert updated.role == "superadmin"
