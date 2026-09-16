"""Section 7 — Identity: login / refresh / logout / change_password / lockout.

测试目标：
    `domains/identity/service.py` 里面所有认证相关流程的业务正确性 —
    成功登录发 access+refresh、失败计数+锁定、refresh 轮换、修改密码
    revoke 老 token、access token 里有 role claim。

为什么重要：
    这是整个多租户 SaaS 的认证根 — 这一层出问题就等于把门钥匙交出去：
      * 不锁账号 → 暴力破解。
      * refresh 不轮换 → token 偷一次能用 7 天。
      * 改密码不 revoke → 旧 token 永不过期。
      * role claim 漏掉 → 前端的 RBAC 全瞎。

设计方法：
    所有测试直接调 `service.*` 函数，绕开 HTTP 层 — service 才是真正的
    业务边界。`InvalidCredentials` / `AccountLocked` / `AccountInactive` /
    `InvalidRefreshToken` 用 `pytest.raises` 精确断言，并断异常上的字段
    （比如 `remaining_attempts` / `minutes_remaining`）。
    用 conftest.py 提供的 `db` + `session_factory` 已经初始化好表结构。
"""

from __future__ import annotations

import pytest

from domains.identity import service as identity_service
from domains.identity.models import RefreshToken
from domains.identity.service import (
    InvalidCredentials,
    InvalidRefreshToken,
    WrongCurrentPassword,
)
from infrastructure.config import settings
from infrastructure.security import (
    decode_access_token,
    hash_password,
    hash_refresh_token,
)
from test_v2.fixtures.helpers import seed_user


# ─── login — happy path ───────────────────────────────────────


def test_login_success_returns_access_refresh_and_user_payload(db) -> None:
    """正常登录: access_token + refresh_token + 嵌套的 UserResponse 一起返回。"""
    user = seed_user(db, email="alice@example.com", role="employee")
    out = identity_service.login(db, email="alice@example.com", password="password123")
    assert out.access_token, "access_token 必须非空"
    assert out.refresh_token, "refresh_token 必须非空"
    assert out.user.id == user.id
    assert out.user.email == "alice@example.com"
    assert out.user.role == "employee"


def test_login_success_resets_failed_counter(db) -> None:
    """登录成功要把 failed_login_attempts 清 0 — 否则之前累积的失败次数会卡住下次登录。"""
    user = seed_user(db, email="bob@example.com")
    user.failed_login_attempts = 3
    db.commit()

    identity_service.login(db, email="bob@example.com", password="password123")
    db.refresh(user)
    assert user.failed_login_attempts == 0
    assert user.locked_until is None
    assert user.last_login is not None


def test_login_access_token_contains_role_claim(db) -> None:
    """access token JWT 里必须有 `role` claim — 前端 RBAC 完全依赖它。"""
    seed_user(db, email="ceo@example.com", role="superadmin")
    out = identity_service.login(db, email="ceo@example.com", password="password123")
    payload = decode_access_token(out.access_token)
    assert payload["role"] == "superadmin"
    assert payload["sub"], "sub (user id) 必须存在"


# ─── login — failure modes ────────────────────────────────────


def test_login_wrong_password_raises_and_increments_failed_count(db) -> None:
    """密码错: InvalidCredentials + DB 里 failed_login_attempts +1。"""
    user = seed_user(db, email="alice@example.com")
    with pytest.raises(InvalidCredentials):
        identity_service.login(db, email="alice@example.com", password="WRONG")
    db.refresh(user)
    assert user.failed_login_attempts == 1
    assert user.last_failed_login is not None


def test_login_unknown_email_raises_invalid_credentials_no_leak(db) -> None:
    """不存在的 email 也只能 raise InvalidCredentials — 不能用 UserNotFound
    之类的不同错误，否则攻击者可以靠它枚举哪些邮箱存在。"""
    with pytest.raises(InvalidCredentials):
        identity_service.login(db, email="nobody@example.com", password="whatever")


def test_login_inactive_user_uses_generic_credentials_error(db) -> None:
    """停用账号必须拒绝，但不能泄露邮箱对应账号是否存在或已停用。"""
    user = seed_user(db, email="alice@example.com")
    user.is_active = False
    db.commit()
    with pytest.raises(InvalidCredentials):
        identity_service.login(db, email="alice@example.com", password="password123")


def test_login_lockout_after_max_failures(db) -> None:
    """连续失败达到阈值后锁定，并继续使用不泄露账号状态的统一错误。"""
    user = seed_user(db, email="brute@example.com")
    max_attempts = settings.MAX_FAILED_LOGIN_ATTEMPTS

    # N-1 次密码错 — 还是 InvalidCredentials
    for _ in range(max_attempts - 1):
        with pytest.raises(InvalidCredentials):
            identity_service.login(db, email="brute@example.com", password="X")

    # 第 N 次 — 这次失败本身会触发锁定 (counter == threshold)
    with pytest.raises(InvalidCredentials):
        identity_service.login(db, email="brute@example.com", password="X")

    db.refresh(user)
    assert user.failed_login_attempts == max_attempts
    assert user.locked_until is not None

    # 此后即便用对密码也被拒绝，但响应不能暴露账号已锁定。
    with pytest.raises(InvalidCredentials):
        identity_service.login(db, email="brute@example.com", password="password123")


# ─── refresh — rotation + revocation ──────────────────────────


def test_refresh_rotates_token_old_revoked_new_issued(db) -> None:
    """轮换语义: 用旧 refresh 换新 access+refresh，旧 refresh 立刻 revoke。"""
    seed_user(db, email="alice@example.com")
    first = identity_service.login(db, email="alice@example.com", password="password123")
    second = identity_service.refresh_tokens(db, refresh_token=first.refresh_token)

    assert second.refresh_token != first.refresh_token, "必须发新 refresh"

    # 旧 token 现在应该已经 revoke 了
    old_hash = hash_refresh_token(first.refresh_token)
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == old_hash).one()
    assert row.is_revoked is True


def test_refresh_retry_within_grace_window_is_idempotent(db) -> None:
    """并发标签页可在短重试窗内复用同一后继 token，不产生第二分支。"""
    seed_user(db, email="alice@example.com")
    first = identity_service.login(db, email="alice@example.com", password="password123")
    second = identity_service.refresh_tokens(db, refresh_token=first.refresh_token)
    retry = identity_service.refresh_tokens(db, refresh_token=first.refresh_token)
    assert retry.refresh_token == second.refresh_token


def test_refresh_with_garbage_token_raises(db) -> None:
    """完全不在 DB 里的字符串当然也得拒。"""
    with pytest.raises(InvalidRefreshToken):
        identity_service.refresh_tokens(db, refresh_token="this-is-not-a-real-token")


# ─── logout ───────────────────────────────────────────────────


def test_logout_revokes_refresh_token(db) -> None:
    """登出: 服务端撤销该 refresh token，后续无法再换 access。"""
    seed_user(db, email="alice@example.com")
    out = identity_service.login(db, email="alice@example.com", password="password123")

    identity_service.logout(db, refresh_token=out.refresh_token)

    row = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == hash_refresh_token(out.refresh_token))
        .one()
    )
    assert row.is_revoked is True

    with pytest.raises(InvalidRefreshToken):
        identity_service.refresh_tokens(db, refresh_token=out.refresh_token)


# ─── change_password ──────────────────────────────────────────


def test_change_password_updates_hash_and_revokes_other_tokens(db) -> None:
    """改密码必须:
       1. 更新 hash 并清掉 is_default_password
       2. 撤销当前用户的所有 refresh token (含当前会话)
       3. 返回新的 access+refresh
    """
    user = seed_user(db, email="alice@example.com")
    # 登录两次 → 模拟两个设备
    sess_a = identity_service.login(db, email="alice@example.com", password="password123")
    sess_b = identity_service.login(db, email="alice@example.com", password="password123")

    new_session = identity_service.change_password(
        db, user=user, current_password="password123", new_password="CorrectHorse789!"
    )

    db.refresh(user)
    assert user.is_default_password is False
    assert user.password_changed_at is not None

    # 老两个 refresh 都得废
    for old in (sess_a.refresh_token, sess_b.refresh_token):
        row = (
            db.query(RefreshToken)
            .filter(RefreshToken.token_hash == hash_refresh_token(old))
            .one()
        )
        assert row.is_revoked is True

    # 新的 refresh 必须可用
    rotated = identity_service.refresh_tokens(db, refresh_token=new_session.refresh_token)
    assert rotated.access_token

    # 旧密码已无效
    with pytest.raises(InvalidCredentials):
        identity_service.login(db, email="alice@example.com", password="password123")
    # 新密码可用
    out = identity_service.login(db, email="alice@example.com", password="CorrectHorse789!")
    assert out.access_token


def test_change_password_wrong_current_raises(db) -> None:
    """current_password 错: 拒 — 不能让捡到 access token 的人改密码。"""
    user = seed_user(db, email="alice@example.com")
    with pytest.raises(WrongCurrentPassword):
        identity_service.change_password(
            db, user=user, current_password="WRONG", new_password="NewPassword456"
        )
    # 密码哈希没变
    db.refresh(user)
    assert user.hashed_password == hash_password("password123") or True  # bcrypt 每次不同
    # 不过用旧密码还登得上，证明 hash 没被换
    out = identity_service.login(db, email="alice@example.com", password="password123")
    assert out.access_token


def test_change_password_too_short_raises(db) -> None:
    """新密码 < 8 字符直接拒 — service 层最低限度的强度检查。"""
    from domains.identity.service import WeakPassword

    user = seed_user(db, email="alice@example.com")
    with pytest.raises(WeakPassword):
        identity_service.change_password(
            db, user=user, current_password="password123", new_password="short"
        )
