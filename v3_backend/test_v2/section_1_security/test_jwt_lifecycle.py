"""Section 1 — JWT lifecycle: access token issuance + decode, plus the
server-side refresh-token rotation in `domains.identity.service`.

测试目标：
    create_access_token / decode_access_token 必须能正常 round-trip，
    JWT 过期、被篡改、缺字段一律抛 InvalidToken；
    refresh_tokens() 必须做 "撤销旧 → 发放新" 的 rotation，
    旧 refresh 在 rotate 之后第二次调用立刻失效；
    role claim 永远存在于 access token 中（RBAC 依赖它）。

为什么重要：
    Access token 是每一个 HTTP / agent 调用的入场券；
    refresh token rotation 是用户长会话的根。
    rotation 失败 = 攻击者偷一次 refresh 永久访问账户。

设计方法：
    纯函数测试用 infrastructure.security 直接做；
    rotation / 撤销链路用 domains.identity.service 做（DB-backed），
    依赖 conftest 的 `db` fixture + seed 一个真用户。
"""

from __future__ import annotations

import time
from datetime import timedelta

import pytest

from domains.identity import service as identity_service
from domains.identity.models import RefreshToken, User
from infrastructure.security import (
    InvalidToken,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
)

# ─── Helper ──────────────────────────────────────────────────


def _make_user(db, email: str = "u@example.com", password: str = "password123") -> User:
    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name="Test User",
        role="employee",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ─── Access token: encode / decode ───────────────────────────


def test_create_access_token_decodes_back_with_same_subject() -> None:
    """sub claim is the str(user_id) — server reads it on every request."""
    token = create_access_token(42, "admin")
    payload = decode_access_token(token)
    assert payload["sub"] == "42"


def test_create_access_token_includes_role_claim() -> None:
    """Without `role` in the payload, RBAC checks (require_admin etc.) fail open."""
    payload = decode_access_token(create_access_token(1, "superadmin"))
    assert payload["role"] == "superadmin"


def test_create_access_token_attaches_unique_jti() -> None:
    """jti is a uuid4 — two tokens for the same user must have different jtis,
    otherwise revocation lists can't tell them apart."""
    a = decode_access_token(create_access_token(1, "employee"))
    time.sleep(0.001)
    b = decode_access_token(create_access_token(1, "employee"))
    assert a["jti"] != b["jti"]


def test_decode_expired_token_raises_invalid_token() -> None:
    """Negative `expires_delta` → JWT exp claim is in the past → decode rejects."""
    token = create_access_token(1, "employee", expires_delta=timedelta(seconds=-5))
    with pytest.raises(InvalidToken):
        decode_access_token(token)


def test_decode_tampered_token_raises_invalid_token() -> None:
    """Appending junk to the signature breaks the HMAC — decode must fail."""
    token = create_access_token(1, "employee")
    with pytest.raises(InvalidToken):
        decode_access_token(token + "junk")


def test_decode_empty_string_raises_invalid_token() -> None:
    with pytest.raises(InvalidToken):
        decode_access_token("")


def test_decode_random_string_raises_invalid_token() -> None:
    with pytest.raises(InvalidToken):
        decode_access_token("not-a-jwt-at-all")


# ─── Refresh token: opaque format + deterministic hash ───────


def test_generate_refresh_token_is_long_and_random() -> None:
    """uuid4.hex → 32 chars; two calls must not collide."""
    a = generate_refresh_token()
    b = generate_refresh_token()
    assert len(a) == 32
    assert a != b


def test_hash_refresh_token_is_deterministic() -> None:
    """SHA-256 of the same token always equals itself — the lookup path
    in `find_active_refresh_token` depends on this."""
    t = "stable-input"
    assert hash_refresh_token(t) == hash_refresh_token(t)


def test_hash_refresh_token_fits_64_char_column() -> None:
    """RefreshToken.token_hash is String(64). SHA-256 hex is exactly 64."""
    assert len(hash_refresh_token("any")) == 64


# ─── Refresh rotation (service-level, DB-backed) ─────────────


def test_refresh_tokens_issues_new_pair(db) -> None:
    """rotate returns a fresh access AND a fresh refresh token."""
    _make_user(db)
    original = identity_service.login(db, email="u@example.com", password="password123")
    rotated = identity_service.refresh_tokens(db, refresh_token=original.refresh_token)
    assert rotated.refresh_token != original.refresh_token
    assert rotated.access_token != original.access_token


def test_refresh_tokens_revokes_old_refresh(db, monkeypatch) -> None:
    """After rotation the old refresh token must be DEAD — single-use semantics
    are what stop a stolen refresh from being valuable forever."""
    monkeypatch.setattr(identity_service.settings, "REFRESH_RETRY_SECONDS", 0)
    _make_user(db)
    original = identity_service.login(db, email="u@example.com", password="password123")
    identity_service.refresh_tokens(db, refresh_token=original.refresh_token)
    with pytest.raises(identity_service.InvalidRefreshToken):
        identity_service.refresh_tokens(db, refresh_token=original.refresh_token)


def test_refresh_tokens_rejects_unknown_token(db) -> None:
    """A token nobody ever issued must be rejected even before user lookup."""
    _make_user(db)
    with pytest.raises(identity_service.InvalidRefreshToken):
        identity_service.refresh_tokens(db, refresh_token="never-issued-token")


def test_revoked_refresh_token_cannot_be_reused(db) -> None:
    """logout() flips `is_revoked` — subsequent refresh calls must reject."""
    _make_user(db)
    tokens = identity_service.login(db, email="u@example.com", password="password123")
    identity_service.logout(db, refresh_token=tokens.refresh_token)
    with pytest.raises(identity_service.InvalidRefreshToken):
        identity_service.refresh_tokens(db, refresh_token=tokens.refresh_token)


def test_refresh_token_row_stores_only_hash_not_plaintext(db) -> None:
    """The DB row must NEVER store the raw refresh token — only the SHA-256 hash.
    A DB compromise then can't be turned into a session-takeover."""
    _make_user(db)
    tokens = identity_service.login(db, email="u@example.com", password="password123")
    rows = db.query(RefreshToken).all()
    assert len(rows) == 1
    stored = rows[0].token_hash
    assert stored != tokens.refresh_token
    assert stored == hash_refresh_token(tokens.refresh_token)


def test_refresh_token_for_deactivated_user_is_rejected(db) -> None:
    """Even with a still-valid refresh token, a deactivated user must NOT
    receive new credentials — otherwise admin's `is_active=False` doesn't bite."""
    user = _make_user(db)
    tokens = identity_service.login(db, email=user.email, password="password123")
    user.is_active = False
    db.commit()
    with pytest.raises(identity_service.InvalidRefreshToken):
        identity_service.refresh_tokens(db, refresh_token=tokens.refresh_token)
