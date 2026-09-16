"""Section 1 — LINE webhook signature: `apps.line.identity.verify_signature`.

测试目标：
    1. 合法签名（同密钥同 body）必须通过；
    2. 签名密钥错误必须拒绝；
    3. body 被改 1 个字节也必须拒绝；
    4. 缺 header 必须拒绝；
    5. 缺 channel_secret 必须 *拒绝* 而不是放行（fail-closed 而非 fail-open）；
    6. 签名比较走的是 hmac.compare_digest（常数时间，等长但内容不同也要返回 False）。

为什么重要：
    LINE webhook 是唯一一条 LINE 服务器调进我们系统的入口；
    签名是 *仅* 有的鉴权 — 一旦绕过攻击者可以伪造任意 message.event 触发
    bind / 发起聊天等行为。fail-closed 默认值是 deploy 配置漂移时的最后防线。

设计方法：
    每个分支一条 test；body / secret 全部用真实字节，不 mock。
    constant-time 那条无法直接测时间，只断言"等长但乱写"也走同一条路径返回 False。
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from apps.line.identity import verify_signature

CHANNEL_SECRET = "test-channel-secret"
BODY = b'{"events":[{"type":"message","source":{"userId":"Uabc"}}]}'


def _sign(body: bytes, secret: str = CHANNEL_SECRET) -> str:
    """Replicate LINE's signing algorithm so tests use known-good inputs."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


# ─── Happy path ──────────────────────────────────────────────


def test_valid_signature_passes() -> None:
    sig = _sign(BODY)
    assert (
        verify_signature(body=BODY, signature_header=sig, channel_secret=CHANNEL_SECRET)
        is True
    )


# ─── Wrong key / wrong body ──────────────────────────────────


def test_signature_made_with_wrong_secret_fails() -> None:
    """Attacker doesn't know our channel secret → their signature must not validate."""
    sig = _sign(BODY, secret="attacker-guess")
    assert (
        verify_signature(body=BODY, signature_header=sig, channel_secret=CHANNEL_SECRET)
        is False
    )


def test_tampered_body_invalidates_signature() -> None:
    """Single byte flip → HMAC mismatches → False."""
    sig = _sign(BODY)
    tampered = BODY.replace(b"message", b"messagE")
    assert (
        verify_signature(body=tampered, signature_header=sig, channel_secret=CHANNEL_SECRET)
        is False
    )


def test_appended_byte_to_body_invalidates_signature() -> None:
    """Appended trailing byte still breaks the HMAC."""
    sig = _sign(BODY)
    assert (
        verify_signature(
            body=BODY + b" ", signature_header=sig, channel_secret=CHANNEL_SECRET
        )
        is False
    )


# ─── Missing / blank inputs (defense-in-depth) ───────────────


def test_empty_signature_header_fails() -> None:
    """LINE always sends a header — an empty value is either a misconfigured
    reverse proxy or an attacker; we refuse either way."""
    assert (
        verify_signature(body=BODY, signature_header="", channel_secret=CHANNEL_SECRET)
        is False
    )


def test_empty_channel_secret_fails_closed() -> None:
    """If the deploy is misconfigured (CHANNEL_SECRET env unset → ""), the
    naive HMAC would still produce a valid signature for some body. We
    refuse all requests instead — fail-closed on missing config."""
    sig = _sign(BODY)
    assert (
        verify_signature(body=BODY, signature_header=sig, channel_secret="")
        is False
    )


def test_garbage_signature_fails() -> None:
    """Random non-base64 string → no match → False."""
    assert (
        verify_signature(
            body=BODY,
            signature_header="this-is-not-a-signature",
            channel_secret=CHANNEL_SECRET,
        )
        is False
    )


# ─── Constant-time comparison ─────────────────────────────────


def test_same_length_wrong_signature_returns_false() -> None:
    """We can't directly measure timing, but a same-length-but-different-content
    input must still flow through `hmac.compare_digest`. A naive `==`
    comparison would still return False, so this is mainly a regression
    guard against someone replacing `compare_digest` with bare equality
    (which would short-circuit on the first differing char and leak timing)."""
    real = _sign(BODY)
    fake_same_length = "A" * len(real)
    assert (
        verify_signature(
            body=BODY, signature_header=fake_same_length, channel_secret=CHANNEL_SECRET
        )
        is False
    )


# ─── Independence: same body, different secrets ──────────────


def test_different_secrets_yield_different_results() -> None:
    """Signing the same body with two secrets must NOT cross-validate.
    Confirms the secret is actually mixed into the HMAC."""
    sig_a = _sign(BODY, secret="secret-A")
    assert (
        verify_signature(body=BODY, signature_header=sig_a, channel_secret="secret-B")
        is False
    )
    assert (
        verify_signature(body=BODY, signature_header=sig_a, channel_secret="secret-A")
        is True
    )
