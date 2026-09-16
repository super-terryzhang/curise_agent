"""Section 1 — LINE bind token lifecycle: `domains.line.service.generate_bind_token`
+ `consume_bind_token`.

测试目标：
    1. 生成的 plaintext 是随机的（同 LINE user 两次 generate 不可碰撞）；
    2. DB 里只存 SHA-256 hash，plaintext 不留任何痕迹；
    3. 单次使用 — 第二次 consume 同一 plaintext 必须报 BindTokenAlreadyUsed；
    4. 过期 token 必须报 BindTokenExpired，且 *不标记* consumed_at；
    5. 不存在的 token 必须报 BindTokenInvalid；
    6. ttl_minutes 真的会影响 expires_at（默认 30 分钟可被覆盖）。

为什么重要：
    Bind token 是把 LINE userId ↔ 内部 User 关联起来的唯一通道。
    如果可以 brute force / 重用 / 在过期后悄悄被消费，攻击者就能劫持别人的账号。
    "过期不能消费但 *不* 标记 consumed" 是因为：
    标记 consumed_at 会让用户在重发后看到误导提示（"已被使用"而非"过期"）。

设计方法：
    每个分支一个测试 — invalid / expired / already-used / happy；
    用 `db.expire_all()` 让 ORM 重新读 DB 行，确认状态确实被持久化。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

import pytest

from domains.line import service as line_service
from domains.line.models import LineBindToken


# ─── Generate: random plaintext, stored as hash only ─────────


def test_generate_returns_high_entropy_plaintext(db) -> None:
    """secrets.token_urlsafe(32) → ~43 chars (256 bits of entropy)."""
    plain = line_service.generate_bind_token(
        db, line_user_id="Uabc", channel_id="default"
    )
    assert len(plain) >= 32


def test_generate_twice_returns_different_plaintext(db) -> None:
    """Same LINE userId, two generate calls → distinct plaintext.
    A repeated plaintext would mean predictable tokens → trivial hijack."""
    a = line_service.generate_bind_token(
        db, line_user_id="Uabc", channel_id="default"
    )
    b = line_service.generate_bind_token(
        db, line_user_id="Uabc", channel_id="default"
    )
    assert a != b


def test_db_stores_sha256_hash_not_plaintext(db) -> None:
    """The DB row's token_hash must equal SHA-256(plaintext); plaintext
    must not appear in any column of the row."""
    plain = line_service.generate_bind_token(
        db, line_user_id="Uabc", channel_id="default"
    )
    row = db.query(LineBindToken).one()
    expected = hashlib.sha256(plain.encode("utf-8")).hexdigest()
    assert row.token_hash == expected
    # No column may contain the plaintext.
    assert plain not in row.token_hash
    assert plain not in row.line_user_id
    assert plain not in row.line_channel_id


def test_generate_persists_line_user_and_channel(db) -> None:
    """The (line_user_id, channel_id) we asked to bind must round-trip into the row."""
    line_service.generate_bind_token(
        db, line_user_id="Uxyz", channel_id="prod"
    )
    row = db.query(LineBindToken).one()
    assert row.line_user_id == "Uxyz"
    assert row.line_channel_id == "prod"


# ─── Consume: happy path ─────────────────────────────────────


def test_consume_returns_bound_line_user_and_channel(db) -> None:
    plain = line_service.generate_bind_token(
        db, line_user_id="Uabc", channel_id="default"
    )
    luid, chid = line_service.consume_bind_token(db, plaintext=plain)
    assert luid == "Uabc"
    assert chid == "default"


def test_consume_sets_consumed_at(db) -> None:
    """Successful consume must persist `consumed_at` — that's the marker
    `consume_bind_token` uses to enforce single-use on retry."""
    plain = line_service.generate_bind_token(
        db, line_user_id="Uabc", channel_id="default"
    )
    line_service.consume_bind_token(db, plaintext=plain)
    db.expire_all()
    row = db.query(LineBindToken).one()
    assert row.consumed_at is not None


# ─── Consume: failure modes ──────────────────────────────────


def test_consume_unknown_token_raises_invalid(db) -> None:
    """Plaintext that nobody minted → BindTokenInvalid."""
    with pytest.raises(line_service.BindTokenInvalid):
        line_service.consume_bind_token(db, plaintext="not-a-real-token")


def test_consume_twice_raises_already_used(db) -> None:
    """Single-use enforcement: second call must raise even within the TTL."""
    plain = line_service.generate_bind_token(
        db, line_user_id="Uabc", channel_id="default"
    )
    line_service.consume_bind_token(db, plaintext=plain)
    with pytest.raises(line_service.BindTokenAlreadyUsed):
        line_service.consume_bind_token(db, plaintext=plain)


def test_consume_expired_token_raises_expired(db) -> None:
    """Back-date the row to simulate expiry without sleeping."""
    plain = line_service.generate_bind_token(
        db, line_user_id="Uabc", channel_id="default"
    )
    row = db.query(LineBindToken).one()
    row.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    with pytest.raises(line_service.BindTokenExpired):
        line_service.consume_bind_token(db, plaintext=plain)


def test_consume_expired_does_not_mark_consumed_at(db) -> None:
    """An expired-but-rejected token must remain `consumed_at IS NULL`.

    Why: if we set consumed_at on expiry, a user retrying after seeing the
    expiry would get the misleading "already used" message instead. The
    error must reflect the REAL cause."""
    plain = line_service.generate_bind_token(
        db, line_user_id="Uabc", channel_id="default"
    )
    row = db.query(LineBindToken).one()
    row.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    with pytest.raises(line_service.BindTokenExpired):
        line_service.consume_bind_token(db, plaintext=plain)
    db.expire_all()
    row_after = db.query(LineBindToken).one()
    assert row_after.consumed_at is None


def test_already_used_takes_precedence_over_expired(db) -> None:
    """If a token is BOTH consumed AND expired, the AlreadyUsed error wins —
    consumed_at is checked before expires_at in service.consume_bind_token.
    Documenting the order matters: that's what the user sees in the message."""
    plain = line_service.generate_bind_token(
        db, line_user_id="Uabc", channel_id="default"
    )
    line_service.consume_bind_token(db, plaintext=plain)
    row = db.query(LineBindToken).one()
    row.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    with pytest.raises(line_service.BindTokenAlreadyUsed):
        line_service.consume_bind_token(db, plaintext=plain)


# ─── TTL configurability ─────────────────────────────────────


def test_ttl_minutes_controls_expires_at(db) -> None:
    """Custom ttl_minutes must be reflected in the stored expires_at row.

    We use a 10-minute window to dodge clock-skew flakiness while still
    distinguishing it from the 30-min default."""
    before = datetime.utcnow()
    line_service.generate_bind_token(
        db, line_user_id="Uabc", channel_id="default", ttl_minutes=10
    )
    after = datetime.utcnow()
    row = db.query(LineBindToken).one()
    expected_low = before + timedelta(minutes=10)
    expected_high = after + timedelta(minutes=10)
    # row.expires_at must be inside the [expected_low, expected_high] window.
    assert expected_low <= row.expires_at <= expected_high
