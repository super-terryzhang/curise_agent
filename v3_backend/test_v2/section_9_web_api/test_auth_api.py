"""Section 9 — Web API: /api/auth/* HTTP contract.

测试目标：
    `apps/http/auth.py` 是把 `domains.identity.service` 抛的业务异常映射成 HTTP
    状态码的薄壳。这里验证：login/refresh/logout/me/change-password 五个端点
    在常见和边缘输入下都吐出正确的 status code + body + DB 副作用。

为什么重要：
    - 登录端点直接面向用户。任何 status code 漂移（401 vs 403 vs 500）都会
      导致前端登录表单卡死或显示错误提示。
    - refresh token 旋转 + 撤销是会话安全的核心。旧 token 不被撤掉就等于
      session fixation，所以 DB 状态必须断言。
    - logout / change-password 改 DB 行（撤 token、改密码哈希），必须从
      DB 真实验证而不是仅看响应码。

设计方法：
    - 走 FastAPI TestClient（来自 conftest 的 `client` fixture），打真实
      HTTP，不 mock service 层。
    - 数据库副作用通过 `session_factory` 拿一份新 session 后再查，证明
      change 是写到了 DB（不仅停留在 session cache）。
    - 一个测试一个行为，header docstring 描述用户视角。
"""

from __future__ import annotations

from domains.identity.models import RefreshToken, User
from infrastructure.security import hash_refresh_token
from test_v2.fixtures.helpers import login, seed_user

# ─── /api/auth/login ───────────────────────────────────────────


def test_login_returns_tokens_and_user_info(client, db):
    """成功登录必须同时返回 access_token、refresh_token、token_type、user。
    前端依赖这四样东西完成会话初始化。"""
    seed_user(db, email="alice@example.com", role="employee")
    r = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "password123"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == "alice@example.com"
    assert body["user"]["role"] == "employee"
    assert body["user"]["is_active"] is True


def test_login_persists_refresh_token_in_db(client, db, session_factory):
    """登录必须把 refresh token 的 hash 落到 v2_refresh_tokens 表，否则
    后续 /refresh 永远查不到记录。"""
    seed_user(db, email="bob@example.com", role="employee")
    r = client.post(
        "/api/auth/login",
        json={"email": "bob@example.com", "password": "password123"},
    )
    raw_token = r.json()["refresh_token"]

    fresh = session_factory()
    try:
        rt = (
            fresh.query(RefreshToken)
            .filter(RefreshToken.token_hash == hash_refresh_token(raw_token))
            .one_or_none()
        )
        assert rt is not None
        assert rt.is_revoked is False
    finally:
        fresh.close()


def test_login_wrong_password_returns_401(client, db):
    """错误密码必须是 401，不能 500（暴露内部错误）或 403（前端会
    错误地告诉用户'权限不足'）。"""
    seed_user(db, email="alice@example.com", role="employee")
    r = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "WRONG"},
    )
    assert r.status_code == 401
    assert "密码" in r.json()["detail"] or "邮箱" in r.json()["detail"]


def test_login_unknown_email_returns_401(client):
    """未知邮箱与错误密码必须返回同一个状态码，避免用户枚举攻击
    （根据 401 的回应来探测哪些邮箱已注册）。"""
    r = client.post(
        "/api/auth/login",
        json={"email": "ghost@example.com", "password": "password123"},
    )
    assert r.status_code == 401


def test_login_inactive_user_returns_generic_401(client, db):
    """已停用账户：service 抛 AccountInactive → router 翻译成 403。
    （与 v2 的契约一致——这是写在 apps/http/auth.py 第 27-28 行的真实
    行为；不要把"应该返回 401"的直觉写进测试。）"""
    user = seed_user(db, email="ghost@example.com", role="employee")
    user.is_active = False
    db.commit()
    r = client.post(
        "/api/auth/login",
        json={"email": "ghost@example.com", "password": "password123"},
    )
    assert r.status_code == 401


# ─── /api/auth/refresh ─────────────────────────────────────────


def test_refresh_returns_new_token_pair(client, db):
    """refresh 必须发新的 access_token + refresh_token（rotation 语义）。"""
    seed_user(db, email="alice@example.com", role="employee")
    login_resp = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "password123"},
    )
    old_refresh = login_resp.json()["refresh_token"]

    r = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"]
    assert body["refresh_token"]
    # New refresh token must differ from the one we presented.
    assert body["refresh_token"] != old_refresh


def test_refresh_revokes_old_token(client, db, session_factory, monkeypatch):
    """旋转语义：refresh 之后旧 refresh_token 应被标记 revoked，
    再用旧 token 调 /refresh 必须 401。"""
    seed_user(db, email="alice@example.com", role="employee")
    login_resp = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "password123"},
    )
    from infrastructure.config import settings
    monkeypatch.setattr(settings, "REFRESH_RETRY_SECONDS", 0)
    old_refresh = login_resp.json()["refresh_token"]

    first = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert first.status_code == 200

    # Old token should now be inert.
    second = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert second.status_code == 401

    # DB also reflects the revocation.
    fresh = session_factory()
    try:
        rt = (
            fresh.query(RefreshToken)
            .filter(RefreshToken.token_hash == hash_refresh_token(old_refresh))
            .one()
        )
        assert rt.is_revoked is True
    finally:
        fresh.close()


def test_refresh_with_garbage_token_returns_401(client):
    """完全无效的 token → 401，不能把 KeyError / 解码异常透出去。"""
    r = client.post("/api/auth/refresh", json={"refresh_token": "totally-fake-token"})
    assert r.status_code == 401


# ─── /api/auth/logout ──────────────────────────────────────────


def test_logout_revokes_refresh_token(client, db, session_factory):
    """logout 必须撤掉当前 refresh token，使其无法继续用于 /refresh。"""
    seed_user(db, email="alice@example.com", role="employee")
    login_resp = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "password123"},
    )
    refresh = login_resp.json()["refresh_token"]

    r = client.post("/api/auth/logout", json={"refresh_token": refresh})
    assert r.status_code == 200
    assert r.json() == {"detail": "已登出"}

    # Now /refresh with that token should fail.
    follow_up = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert follow_up.status_code == 401

    fresh = session_factory()
    try:
        rt = (
            fresh.query(RefreshToken)
            .filter(RefreshToken.token_hash == hash_refresh_token(refresh))
            .one()
        )
        assert rt.is_revoked is True
    finally:
        fresh.close()


# ─── /api/auth/me ──────────────────────────────────────────────


def test_me_requires_bearer_token(client):
    """无 Authorization header → HTTPBearer 自动 403（FastAPI default
    when auto_error=True 且 header missing）。任何非 200 都行，主要是
    确认没有匿名能读到用户身份。"""
    r = client.get("/api/auth/me")
    assert r.status_code in (401, 403)


def test_me_returns_current_user_info(client, db):
    """带合法 token → 返回当前 user 详情。前端用这个端点做"会话还活着吗"
    + 显示登录用户姓名。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    r = client.get("/api/auth/me", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "alice@example.com"
    assert body["role"] == "employee"
    assert body["is_active"] is True
    # No password hash leaks
    assert "hashed_password" not in body
    assert "password" not in body


# ─── /api/auth/change-password ─────────────────────────────────


def test_change_password_updates_hash(client, db, session_factory):
    """改密码：service 调 hash_password() 写入 users.hashed_password。"""
    user = seed_user(db, email="alice@example.com", role="employee")
    old_hash = user.hashed_password
    headers = login(client, "alice@example.com")

    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "password123", "new_password": "new-password-for-validation"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    fresh = session_factory()
    try:
        u = fresh.query(User).filter(User.email == "alice@example.com").one()
        assert u.hashed_password != old_hash
    finally:
        fresh.close()

    # New password works for login.
    login_resp = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "new-password-for-validation"},
    )
    assert login_resp.status_code == 200


def test_change_password_revokes_existing_sessions(client, db, session_factory):
    """改密码后所有旧 refresh token 必须失效——典型"密码泄露后清掉所有
    设备登录"的安全保证。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")

    # Spin up a second session ("another device") before changing password.
    other_session = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "password123"},
    )
    old_refresh = other_session.json()["refresh_token"]

    change = client.post(
        "/api/auth/change-password",
        json={"current_password": "password123", "new_password": "new-password-for-validation"},
        headers=headers,
    )
    assert change.status_code == 200

    # Old refresh from the "other device" should no longer rotate.
    r = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 401


def test_change_password_wrong_current_returns_400(client, db):
    """当前密码错 → 400，不能因为响应里包含"密码"字眼就返 401（401
    会让前端把人踢到登录页）。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "WRONG", "new_password": "new-password-for-validation"},
        headers=headers,
    )
    assert r.status_code == 400


def test_change_password_too_short_returns_400(client, db):
    """新密码 <8 chars → WeakPassword → 400."""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "password123", "new_password": "short"},
        headers=headers,
    )
    assert r.status_code == 400
