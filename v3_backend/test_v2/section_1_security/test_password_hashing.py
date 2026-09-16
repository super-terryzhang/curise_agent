"""Section 1 — Password hashing: `infrastructure.security.hash_password` /
`verify_password`.

测试目标：
    密码哈希一定走 bcrypt（不可逆）、不同 salt（同样输入两次哈希不同）、
    truncation 行为符合 bcrypt 的 72-byte 限制、错误密码必须被拒绝。

为什么重要：
    bcrypt 是整个登录链路的根。一旦哈希弱化或可逆，所有 RBAC / refresh token
    机制都成了纸糊的。同时 2025-10-07 我们刚刚踩过 bcrypt 5.0.0 的 ABI 问题
    (passlib `__about__` 缺失) — 这层守门测试在 CI 必须红 / 绿一目了然。

设计方法：
    每个测试只断一件事；不读 DB、不起 HTTP — 全部是对纯函数的输入/输出测试。
    72-byte 边界用 parametrize 覆盖三种长度（71/72/100）。
"""

from __future__ import annotations

import pytest

from infrastructure.security import hash_password, verify_password

# ─── Roundtrip: hash → verify ─────────────────────────────────


def test_hash_then_verify_returns_true_for_same_password() -> None:
    """A freshly hashed password verifies against its own plaintext."""
    h = hash_password("password123")
    assert verify_password("password123", h) is True


def test_verify_rejects_wrong_password() -> None:
    """Hash of `correct` must NOT verify against `wrong`."""
    h = hash_password("correct-horse")
    assert verify_password("battery-staple", h) is False


def test_hash_is_not_plaintext() -> None:
    """Hash never equals the input — defense against a stored-as-plaintext regression."""
    h = hash_password("my-password")
    assert h != "my-password"
    assert "my-password" not in h


def test_hash_starts_with_bcrypt_identifier() -> None:
    """passlib emits bcrypt hashes with the $2b$ (or $2a$/$2y$) prefix.

    If a future refactor swaps to e.g. PBKDF2 silently this test catches it.
    """
    h = hash_password("any")
    assert h.startswith("$bcrypt-sha256$"), f"expected bcrypt prefix, got {h[:10]}"


# ─── Salt uniqueness ─────────────────────────────────────────


def test_two_hashes_of_same_password_differ() -> None:
    """Random salt → two hashes of the same plaintext never collide."""
    a = hash_password("same-input")
    b = hash_password("same-input")
    assert a != b


def test_both_hashes_of_same_password_verify() -> None:
    """Despite differing salts, both hashes must accept the original plaintext."""
    a = hash_password("same-input")
    b = hash_password("same-input")
    assert verify_password("same-input", a) is True
    assert verify_password("same-input", b) is True


# ─── bcrypt 72-byte truncation behavior ──────────────────────


@pytest.mark.parametrize("length", [1, 8, 71, 72])
def test_password_under_72_bytes_roundtrips(length: int) -> None:
    """All lengths ≤ 72 bytes hash and verify with the same plaintext."""
    pw = "a" * length
    h = hash_password(pw)
    assert verify_password(pw, h) is True


def test_passwords_differing_after_72_bytes_remain_distinct() -> None:
    """New hashes prehash long passwords; the old truncation must not persist."""
    prefix = "x" * 72
    h = hash_password(prefix + "AAA")
    assert verify_password(prefix + "AAA", h) is True
    assert verify_password(prefix + "ZZZ", h) is False


# ─── Verify-side robustness ──────────────────────────────────


def test_verify_returns_false_for_empty_input() -> None:
    """Verifying an empty plaintext against a real hash must NOT pass."""
    h = hash_password("real-pw")
    assert verify_password("", h) is False
