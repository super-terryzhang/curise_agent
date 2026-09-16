"""Section 9 — Web API: /api/users/* HTTP contract (superadmin-only).

测试目标：
    `apps/http/users.py` 是 superadmin 专属的用户管理面板入口。这里验证：
    五个端点（list/create/patch/delete/reset-password）的鉴权 + 业务规则
    + DB 状态，特别是 deactivate / reset-password 会带来 cascade 效果
    （撤销该用户的全部 refresh token）。

为什么重要：
    - 鉴权失败必须返 403，不能 500 或 401（前端逻辑不一样）。
    - 自己停用自己（self-deactivate）是死锁场景：禁了之后无人能 re-enable。
      必须明确报 400。
    - reset-password 是"密码遗忘"工作流的核心；如果忘了撤现有 refresh
      token，攻击者可以继续用旧 session 操作账号。

设计方法：
    - 多角色场景：用 helpers.seed_user 注三种角色（superadmin/admin/employee），
      用 helpers.login 分别拿三套 headers，验证 admin / employee 都不能访问
      这套端点。
    - 副作用验证：在副 session 里查 DB（User.is_active、RefreshToken.is_revoked）
      确保不只是响应说"成功"而是 DB 真改了。
"""

from __future__ import annotations

from domains.identity.models import RefreshToken, User
from test_v2.fixtures.helpers import login, seed_user

# ─── Auth gate (all routes are superadmin-only) ──────────────


def test_admin_role_cannot_list_users(client, db):
    """admin 不是 superadmin → 403。这是为了把"用户管理"完全留给 superadmin，
    防止 admin 互改账号。"""
    seed_user(db, email="adm@x.test", role="admin")
    headers = login(client, "adm@x.test")
    r = client.get("/api/users", headers=headers)
    assert r.status_code == 403


def test_employee_role_cannot_list_users(client, db):
    seed_user(db, email="emp@x.test", role="employee")
    headers = login(client, "emp@x.test")
    r = client.get("/api/users", headers=headers)
    assert r.status_code == 403


def test_unauthenticated_request_blocked(client):
    """没 Authorization → 401/403 之一（HTTPBearer auto_error 决定）。"""
    r = client.get("/api/users")
    assert r.status_code in (401, 403)


# ─── GET /api/users ──────────────────────────────────────────


def test_list_users_returns_array_no_password_hashes(client, db):
    """superadmin 列表：响应里绝不能出现 hashed_password / password。"""
    seed_user(db, email="su@x.test", role="superadmin")
    seed_user(db, email="emp@x.test", role="employee")
    headers = login(client, "su@x.test")
    r = client.get("/api/users", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) >= 2
    for entry in body:
        assert "hashed_password" not in entry
        assert "password" not in entry
        # Required UserListResponse fields
        assert "email" in entry
        assert "role" in entry
        assert "is_active" in entry


# ─── POST /api/users ─────────────────────────────────────────


def test_create_user_persists_row(client, db, session_factory):
    """完整 payload 创建用户：成功 201 + DB 里能查到。"""
    seed_user(db, email="su@x.test", role="superadmin")
    headers = login(client, "su@x.test")
    r = client.post(
        "/api/users",
        json={
            "email": "newbie@x.test",
            "full_name": "New User",
            "password": "New-user-password-for-validation",
            "role": "employee",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "newbie@x.test"
    assert body["role"] == "employee"

    fresh = session_factory()
    try:
        u = fresh.query(User).filter(User.email == "newbie@x.test").one()
        assert u.full_name == "New User"
        assert u.role == "employee"
        assert u.is_active is True
    finally:
        fresh.close()


def test_create_user_duplicate_email_returns_409(client, db):
    """已存在邮箱 → EmailAlreadyExists → 409 Conflict（不是 400 or 500）。"""
    seed_user(db, email="su@x.test", role="superadmin")
    seed_user(db, email="dup@x.test", role="employee")
    headers = login(client, "su@x.test")
    r = client.post(
        "/api/users",
        json={
            "email": "dup@x.test",
            "full_name": "Duplicate",
            "password": "New-user-password-for-validation",
            "role": "employee",
        },
        headers=headers,
    )
    assert r.status_code == 409


# ─── PATCH /api/users/{id} ───────────────────────────────────


def test_patch_user_updates_full_name(client, db, session_factory):
    """PATCH 改 full_name 不会触发其他副作用（is_active、role 等不动）。"""
    seed_user(db, email="su@x.test", role="superadmin")
    target = seed_user(db, email="emp@x.test", role="employee")
    headers = login(client, "su@x.test")
    r = client.patch(
        f"/api/users/{target.id}",
        json={"full_name": "Updated Name"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["full_name"] == "Updated Name"

    fresh = session_factory()
    try:
        u = fresh.query(User).filter(User.id == target.id).one()
        assert u.full_name == "Updated Name"
        assert u.role == "employee"  # unchanged
        assert u.is_active is True   # unchanged
    finally:
        fresh.close()


def test_patch_user_role_change_persists(client, db, session_factory):
    """role 升降必须真正写到 DB——否则 rebac 失效，admin 操作以 employee
    权限走了路由。"""
    seed_user(db, email="su@x.test", role="superadmin")
    target = seed_user(db, email="emp@x.test", role="employee")
    headers = login(client, "su@x.test")
    r = client.patch(
        f"/api/users/{target.id}",
        json={"role": "admin"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["role"] == "admin"

    fresh = session_factory()
    try:
        u = fresh.query(User).filter(User.id == target.id).one()
        assert u.role == "admin"
    finally:
        fresh.close()


# ─── DELETE /api/users/{id} ──────────────────────────────────


def test_delete_user_deactivates_and_revokes_tokens(client, db, session_factory):
    """DELETE 是 soft delete（is_active=False） + 撤销该用户的所有
    refresh token。这是组合操作：DB 里 user 行还在但 status flipped，
    refresh_tokens 全部 revoked。"""
    seed_user(db, email="su@x.test", role="superadmin")
    target = seed_user(db, email="emp@x.test", role="employee")
    # employee 先登录一次产生一个 refresh token
    emp_headers = login(client, "emp@x.test")  # noqa: F841 — only need the side-effect

    su_headers = login(client, "su@x.test")
    r = client.delete(f"/api/users/{target.id}", headers=su_headers)
    assert r.status_code == 200
    assert r.json() == {"detail": "用户已停用"}

    fresh = session_factory()
    try:
        u = fresh.query(User).filter(User.id == target.id).one()
        assert u.is_active is False
        tokens = fresh.query(RefreshToken).filter(RefreshToken.user_id == target.id).all()
        assert tokens, "employee 应在登录时就生成过一个 refresh token"
        assert all(t.is_revoked for t in tokens)
    finally:
        fresh.close()


def test_delete_self_returns_400(client, db):
    """superadmin 不能停用自己（CannotDeactivateSelf → 400）——否则可能
    把系统锁死，无人能再启用账户。"""
    su = seed_user(db, email="su@x.test", role="superadmin")
    headers = login(client, "su@x.test")
    r = client.delete(f"/api/users/{su.id}", headers=headers)
    assert r.status_code == 400


def test_delete_unknown_user_returns_404(client, db):
    seed_user(db, email="su@x.test", role="superadmin")
    headers = login(client, "su@x.test")
    r = client.delete("/api/users/999999", headers=headers)
    assert r.status_code == 404


# ─── POST /api/users/{id}/reset-password ─────────────────────


def test_reset_password_returns_temp_password_and_marks_default(
    client, db, session_factory
):
    """reset 后：
        - 响应里含临时密码（前端 OOB 给用户用）
        - DB 里 is_default_password=True（用户首次登录必须改）
        - 该用户所有 refresh token 全部失效（旧 session 立即注销）。"""
    seed_user(db, email="su@x.test", role="superadmin")
    target = seed_user(db, email="emp@x.test", role="employee")
    # employee 先登录拿到 refresh token
    login(client, "emp@x.test")
    old_hash = (
        db.query(User).filter(User.id == target.id).one().hashed_password
    )

    su_headers = login(client, "su@x.test")
    r = client.post(f"/api/users/{target.id}/reset-password", headers=su_headers)
    assert r.status_code == 200
    detail = r.json()["detail"]
    assert "临时密码" in detail

    fresh = session_factory()
    try:
        u = fresh.query(User).filter(User.id == target.id).one()
        assert u.is_default_password is True
        assert u.hashed_password != old_hash
        tokens = fresh.query(RefreshToken).filter(RefreshToken.user_id == target.id).all()
        assert tokens
        assert all(t.is_revoked for t in tokens)
    finally:
        fresh.close()


def test_reset_password_unknown_user_returns_404(client, db):
    seed_user(db, email="su@x.test", role="superadmin")
    headers = login(client, "su@x.test")
    r = client.post("/api/users/999999/reset-password", headers=headers)
    assert r.status_code == 404
