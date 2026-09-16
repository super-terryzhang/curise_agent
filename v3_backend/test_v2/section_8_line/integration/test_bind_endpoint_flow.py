"""Section 8 — POST /api/line/bind/{token}: credentials + token consumption + hijack guard.

测试目标：
    凭密码 + 一次性 bind token 创建 (LINE userId ↔ internal user) 绑定。
    覆盖：
    - 正确密码 + 合法 token → 200，LineUser 行建立，token 标记 consumed。
    - 同一 user 再次绑定（用新 token）→ 200 (idempotent)。
    - 未知 token → 400。
    - 过期 token → 400 且不消费 token。
    - 已使用过的 token → 400。
    - 密码错 → 401 且不消费 token（否则攻击者能耗尽 token）。
    - 同一 LINE userId 想换绑到另一个 internal user → 409。

为什么重要：
    这是 LINE 用户证明自己是公司员工的唯一通道；分支错了要么用户绑不上，
    要么有人绑别人的 LINE id 拿到别人的权限。token 在密码错时不被消费的语义
    至关重要 —— 否则离职员工把 token 截下来就能 DOS 后续真用户。

设计方法：
    用 v2 标准 `client` + `session_factory` fixture。helper 直接调
    service 层（绕过 webhook）来构造 user / token / binding 状态。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from domains.identity.models import User
from domains.line import service as line_service
from domains.line.models import LineBindToken, LineUser
from infrastructure.security import hash_password


# ─── helpers ──────────────────────────────────────────────────


def _seed_user(
    session_factory, *, email: str, password: str = "password123", role: str = "employee"
) -> int:
    db = session_factory()
    try:
        u = User(
            email=email,
            hashed_password=hash_password(password),
            full_name=email.split("@")[0],
            role=role,
            is_active=True,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _mint_token(session_factory, *, line_user_id: str = "Uabc", ttl_minutes: int = 30) -> str:
    db = session_factory()
    try:
        return line_service.generate_bind_token(
            db,
            line_user_id=line_user_id,
            channel_id="default",
            ttl_minutes=ttl_minutes,
        )
    finally:
        db.close()


# ─── Happy path ───────────────────────────────────────────────


def test_bind_happy_path_creates_line_user_and_consumes_token(
    client, session_factory
) -> None:
    uid = _seed_user(session_factory, email="alice@x.com")
    token = _mint_token(session_factory, line_user_id="Uabc")

    r = client.post(
        f"/api/line/bind/{token}",
        json={"email": "alice@x.com", "password": "password123"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["line_user_id"] == "Uabc"
    assert body["user_id"] == uid
    assert body["user_email"] == "alice@x.com"
    assert body["user_role"] == "employee"

    db = session_factory()
    try:
        # LineUser 行已建立
        lu = db.query(LineUser).filter_by(line_user_id="Uabc").one()
        assert lu.user_id == uid
        assert lu.line_channel_id == "default"
        assert lu.is_blocked is False
        # token 行已 consumed
        bt = db.query(LineBindToken).one()
        assert bt.consumed_at is not None
    finally:
        db.close()


def test_rebinding_same_user_with_new_token_is_idempotent(
    client, session_factory
) -> None:
    """re-bind 同一个 (LINE id, internal user) —— 第二次 200，user_id 不变。
    （token 一次性，所以要新 token。）"""
    uid = _seed_user(session_factory, email="alice@x.com")

    t1 = _mint_token(session_factory, line_user_id="Uabc")
    r1 = client.post(
        f"/api/line/bind/{t1}",
        json={"email": "alice@x.com", "password": "password123"},
    )
    assert r1.status_code == 200

    t2 = _mint_token(session_factory, line_user_id="Uabc")
    r2 = client.post(
        f"/api/line/bind/{t2}",
        json={"email": "alice@x.com", "password": "password123"},
    )
    assert r2.status_code == 200
    assert r2.json()["user_id"] == uid

    # DB 里仍然只有一行 LineUser
    db = session_factory()
    try:
        assert db.query(LineUser).filter_by(line_user_id="Uabc").count() == 1
    finally:
        db.close()


# ─── Token failure modes ──────────────────────────────────────


def test_unknown_token_returns_400(client, session_factory) -> None:
    """不存在的 token —— 400 generic error message。"""
    _seed_user(session_factory, email="alice@x.com")
    r = client.post(
        "/api/line/bind/some-bogus-token",
        json={"email": "alice@x.com", "password": "password123"},
    )
    assert r.status_code == 400


def test_expired_token_returns_400_and_does_not_consume(
    client, session_factory
) -> None:
    """过期 token —— 返回 400，且 token 不被标记 consumed（保留审计痕迹）。"""
    _seed_user(session_factory, email="alice@x.com")
    token = _mint_token(session_factory, line_user_id="Uabc")

    # 手动把 expires_at 设为过去
    db = session_factory()
    try:
        row = db.query(LineBindToken).one()
        row.expires_at = datetime.utcnow() - timedelta(minutes=5)
        db.commit()
    finally:
        db.close()

    r = client.post(
        f"/api/line/bind/{token}",
        json={"email": "alice@x.com", "password": "password123"},
    )
    assert r.status_code == 400

    # 过期 token 不应该被消费（service.consume_bind_token 在标 consumed 前就 raise）
    db = session_factory()
    try:
        row = db.query(LineBindToken).one()
        assert row.consumed_at is None
    finally:
        db.close()


def test_already_consumed_token_returns_400(client, session_factory) -> None:
    """one-shot —— 用一次就废。"""
    _seed_user(session_factory, email="alice@x.com")
    token = _mint_token(session_factory, line_user_id="Uabc")

    r1 = client.post(
        f"/api/line/bind/{token}",
        json={"email": "alice@x.com", "password": "password123"},
    )
    assert r1.status_code == 200

    # 第二次用同一 token —— 必须 400
    r2 = client.post(
        f"/api/line/bind/{token}",
        json={"email": "alice@x.com", "password": "password123"},
    )
    assert r2.status_code == 400


# ─── Credential failure modes ─────────────────────────────────


def test_wrong_password_returns_401_and_does_not_consume_token(
    client, session_factory
) -> None:
    """关键 anti-DOS：密码错时 token 不被消费 —— 否则攻击者能耗尽别人的 token。"""
    _seed_user(session_factory, email="alice@x.com", password="correct123")
    token = _mint_token(session_factory, line_user_id="Uabc")

    r = client.post(
        f"/api/line/bind/{token}",
        json={"email": "alice@x.com", "password": "wrong-pw"},
    )
    assert r.status_code == 401

    db = session_factory()
    try:
        bt = db.query(LineBindToken).one()
        assert bt.consumed_at is None
    finally:
        db.close()


# ─── Hijack protection ────────────────────────────────────────


def test_different_internal_user_for_same_line_id_returns_409(
    client, session_factory
) -> None:
    """同一 LINE userId 已绑给 alice，bob 想绑同一 LINE id —— 409 conflict。
    alice 的绑定不被破坏。"""
    alice_id = _seed_user(session_factory, email="alice@x.com")
    _seed_user(session_factory, email="bob@x.com")

    # alice 先成功绑定 Uabc
    t_alice = _mint_token(session_factory, line_user_id="Uabc")
    r_alice = client.post(
        f"/api/line/bind/{t_alice}",
        json={"email": "alice@x.com", "password": "password123"},
    )
    assert r_alice.status_code == 200

    # bob 拿新 token 想用同一 LINE id 绑定到自己
    t_bob = _mint_token(session_factory, line_user_id="Uabc")
    r_bob = client.post(
        f"/api/line/bind/{t_bob}",
        json={"email": "bob@x.com", "password": "password123"},
    )
    assert r_bob.status_code == 409

    # alice 的绑定仍然完好无损
    db = session_factory()
    try:
        rows = db.query(LineUser).filter_by(line_user_id="Uabc").all()
        assert len(rows) == 1
        assert rows[0].user_id == alice_id
    finally:
        db.close()
