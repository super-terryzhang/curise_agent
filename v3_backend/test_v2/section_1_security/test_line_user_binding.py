"""Section 1 — LINE user binding: `domains.line.service.bind_user` /
`find_user_by_line_id`.

测试目标：
    1. 首次 bind 必须新建 LineUser 行；
    2. 同 (line_user_id, channel) 重 bind 到同一内部用户 → 幂等（不报错，更新 display_name + last_active_at）；
    3. 同 (line_user_id, channel) bind 到 *不同* 内部用户 → 必须报 LineUserAlreadyBound（防止劫持）；
    4. 同 line_user_id 但不同 channel → 完全独立的两行（LINE 的 userId 是 per-channel 的）；
    5. find_user_by_line_id 必须按 (line_user_id, channel) 复合 key 查询，不能只看 line_user_id；
    6. update_last_active 真的会更新 last_active_at 字段。

为什么重要：
    LINE userId ↔ 内部 User 的映射决定"谁在跟我聊天"。
    一旦 hijack 允许：攻击者只要知道目标用户的 LINE userId，
    就能把它 bind 到自己账号，从此偷别人 LINE 消息的回复。
    Channel scoping 同样关键：多 channel 部署时若忽略 channel_id，
    prod / test bot 的 userId 互相覆盖，bind 会乱。

设计方法：
    每条 user-flow 一个 test；依赖 conftest 的 `db` fixture 直接对 service 调用，
    不绕到 HTTP。LineUserAlreadyBound / 幂等更新都通过断言 ORM 行的 .id / 字段值
    来验证持久化结果。
"""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from domains.identity.models import User
from domains.line import service as line_service
from infrastructure.security import hash_password


def _make_user(db, *, email: str = "u@example.com", role: str = "employee") -> User:
    u = User(
        email=email,
        hashed_password=hash_password("password123"),
        full_name=email.split("@")[0],
        role=role,
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ─── find_user_by_line_id ────────────────────────────────────


def test_find_returns_none_when_no_binding_exists(db) -> None:
    assert (
        line_service.find_user_by_line_id(
            db, line_user_id="Unobody", channel_id="default"
        )
        is None
    )


def test_find_returns_row_after_bind(db) -> None:
    u = _make_user(db)
    line_service.bind_user(
        db, line_user_id="Uabc", channel_id="default", internal_user_id=u.id
    )
    found = line_service.find_user_by_line_id(
        db, line_user_id="Uabc", channel_id="default"
    )
    assert found is not None
    assert found.user_id == u.id


def test_find_distinguishes_by_channel(db) -> None:
    """Same line_user_id, different channel — lookup must be channel-scoped
    or it leaks bindings across bot channels."""
    a = _make_user(db, email="a@x.com")
    b = _make_user(db, email="b@x.com")
    line_service.bind_user(
        db, line_user_id="Usame", channel_id="prod", internal_user_id=a.id
    )
    line_service.bind_user(
        db, line_user_id="Usame", channel_id="test", internal_user_id=b.id
    )
    assert (
        line_service.find_user_by_line_id(
            db, line_user_id="Usame", channel_id="prod"
        ).user_id == a.id
    )
    assert (
        line_service.find_user_by_line_id(
            db, line_user_id="Usame", channel_id="test"
        ).user_id == b.id
    )


# ─── bind_user: first time ───────────────────────────────────


def test_first_bind_creates_row(db) -> None:
    u = _make_user(db)
    lu = line_service.bind_user(
        db,
        line_user_id="Uabc",
        channel_id="default",
        internal_user_id=u.id,
        display_name="Alice",
    )
    assert lu.id is not None
    assert lu.line_user_id == "Uabc"
    assert lu.line_channel_id == "default"
    assert lu.user_id == u.id
    assert lu.display_name == "Alice"


def test_first_bind_initializes_is_blocked_false(db) -> None:
    """A fresh binding must default to not-blocked (otherwise the user
    couldn't chat right after bind)."""
    u = _make_user(db)
    lu = line_service.bind_user(
        db, line_user_id="Uabc", channel_id="default", internal_user_id=u.id
    )
    assert lu.is_blocked is False


def test_first_bind_sets_last_active_at(db) -> None:
    """last_active_at must be populated on bind so 'inactive users' filters work."""
    u = _make_user(db)
    before = datetime.utcnow()
    lu = line_service.bind_user(
        db, line_user_id="Uabc", channel_id="default", internal_user_id=u.id
    )
    after = datetime.utcnow()
    assert lu.last_active_at is not None
    assert before <= lu.last_active_at <= after


# ─── bind_user: idempotent re-bind ───────────────────────────


def test_rebind_same_user_returns_same_row(db) -> None:
    """Re-binding the same (LINE id, channel) to the SAME internal user must
    return the existing row, not create a duplicate."""
    u = _make_user(db)
    lu1 = line_service.bind_user(
        db, line_user_id="Uabc", channel_id="default", internal_user_id=u.id
    )
    lu2 = line_service.bind_user(
        db, line_user_id="Uabc", channel_id="default", internal_user_id=u.id
    )
    assert lu1.id == lu2.id


def test_rebind_updates_display_name(db) -> None:
    """LINE may change a user's display name; re-bind must persist the new value."""
    u = _make_user(db)
    line_service.bind_user(
        db,
        line_user_id="Uabc",
        channel_id="default",
        internal_user_id=u.id,
        display_name="Old Name",
    )
    lu2 = line_service.bind_user(
        db,
        line_user_id="Uabc",
        channel_id="default",
        internal_user_id=u.id,
        display_name="New Name",
    )
    assert lu2.display_name == "New Name"


def test_rebind_without_display_name_keeps_existing(db) -> None:
    """If the caller passes display_name=None we must NOT wipe the existing one
    — that would be data loss."""
    u = _make_user(db)
    line_service.bind_user(
        db,
        line_user_id="Uabc",
        channel_id="default",
        internal_user_id=u.id,
        display_name="Original",
    )
    lu2 = line_service.bind_user(
        db,
        line_user_id="Uabc",
        channel_id="default",
        internal_user_id=u.id,
        display_name=None,
    )
    assert lu2.display_name == "Original"


def test_rebind_bumps_last_active_at(db) -> None:
    """Each bind/rebind = the user is alive right now → bump the timestamp."""
    u = _make_user(db)
    lu = line_service.bind_user(
        db, line_user_id="Uabc", channel_id="default", internal_user_id=u.id
    )
    earlier = lu.last_active_at
    time.sleep(0.01)  # ensure datetime.utcnow() advances on fast machines
    lu2 = line_service.bind_user(
        db, line_user_id="Uabc", channel_id="default", internal_user_id=u.id
    )
    assert lu2.last_active_at > earlier


# ─── bind_user: hijack rejection ─────────────────────────────


def test_rebind_to_different_user_raises_hijack_error(db) -> None:
    """Attacker who guesses someone else's LINE userId must NOT be able
    to claim that binding for their own account."""
    a = _make_user(db, email="alice@x.com")
    b = _make_user(db, email="attacker@x.com")
    line_service.bind_user(
        db, line_user_id="Uabc", channel_id="default", internal_user_id=a.id
    )
    with pytest.raises(line_service.LineUserAlreadyBound):
        line_service.bind_user(
            db, line_user_id="Uabc", channel_id="default", internal_user_id=b.id
        )


def test_hijack_attempt_leaves_existing_binding_intact(db) -> None:
    """After a rejected hijack the original binding must remain unchanged."""
    a = _make_user(db, email="alice@x.com")
    b = _make_user(db, email="attacker@x.com")
    lu = line_service.bind_user(
        db, line_user_id="Uabc", channel_id="default", internal_user_id=a.id
    )
    with pytest.raises(line_service.LineUserAlreadyBound):
        line_service.bind_user(
            db, line_user_id="Uabc", channel_id="default", internal_user_id=b.id
        )
    db.expire_all()
    after = line_service.find_user_by_line_id(
        db, line_user_id="Uabc", channel_id="default"
    )
    assert after.id == lu.id
    assert after.user_id == a.id  # still Alice's


# ─── Per-channel scoping ─────────────────────────────────────


def test_same_line_id_different_channels_create_two_rows(db) -> None:
    """Same line_user_id on prod vs test channels → two distinct rows,
    each bindable to a different internal user."""
    a = _make_user(db, email="a@x.com")
    b = _make_user(db, email="b@x.com")
    lu_prod = line_service.bind_user(
        db, line_user_id="Uabc", channel_id="prod", internal_user_id=a.id
    )
    lu_test = line_service.bind_user(
        db, line_user_id="Uabc", channel_id="test", internal_user_id=b.id
    )
    assert lu_prod.id != lu_test.id
    assert lu_prod.user_id == a.id
    assert lu_test.user_id == b.id


# ─── update_last_active ──────────────────────────────────────


def test_update_last_active_advances_timestamp(db) -> None:
    """Explicit update_last_active() bumps the field so 'last seen' UIs are correct."""
    u = _make_user(db)
    lu = line_service.bind_user(
        db, line_user_id="Uabc", channel_id="default", internal_user_id=u.id
    )
    earlier = lu.last_active_at
    time.sleep(0.01)
    line_service.update_last_active(db, line_user=lu)
    db.expire_all()
    refreshed = line_service.find_user_by_line_id(
        db, line_user_id="Uabc", channel_id="default"
    )
    assert refreshed.last_active_at > earlier
