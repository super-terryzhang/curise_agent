"""Section 9 — Web API: finance role RBAC contract.

测试目标：
    finance 角色（2026-06-08 Felix 决定）拥有员工的全部写权限（FinancialTab 的负责人，
    主要给日本员工），但**不能**访问 superadmin 专属端点。这里通过真实 HTTP 请求
    校验三件事：
    1. finance 用户能命中 Writer 端点（与 employee 等价）
    2. finance 用户被 Superadmin 端点拒绝（403）
    3. employee 行为没有回归（继续能用、继续被 Superadmin 拒）

为什么重要：
    - `require_writer` 是订单写、cost item 写、chat、文件上传等几十个端点的同一道闸。
      漏 finance 一条会让 finance 用户在前端到处碰到 403，财务工作直接做不了。
    - Superadmin 闸（用户管理）必须把 finance 关在门外，否则 finance 能改其他用户密码。
    - 现有 employee/admin/superadmin 测试覆盖在 section_7 + section_1，这里只补 finance。

测试方法：
    - 真实 HTTP（TestClient + login()），不 mock 中间件。
    - 用 POST /api/chat/sessions（Writer 闸，body 简单）当 Writer 端点代表。
    - 用 GET /api/users（Superadmin 闸）当 Superadmin 端点代表。
    - 一个测试一个行为；断言走 status code，不走 body 细节。
"""

from __future__ import annotations

from test_v2.fixtures.helpers import login, seed_user


# ─── Writer gate ───────────────────────────────────────────────


def test_finance_user_can_access_writer_endpoint(client, db):
    """finance 用户 POST /api/chat/sessions 应该 200 —— 与 employee 等价。

    背景：财务岗（日本员工）负责 FinancialTab，日常也会用 chat agent 查订单。
    如果 Writer 闸把 finance 排除（2026-06-16 修复前的状态），财务岗连 chat
    都开不了。"""
    seed_user(db, email="finance-jp@x.test", role="finance")
    headers = login(client, "finance-jp@x.test")
    r = client.post(
        "/api/chat/sessions",
        headers=headers,
        json={"title": "test session", "model": "gemini-3.5-flash"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "test session"


def test_employee_user_still_can_access_writer_endpoint(client, db):
    """回归守护：employee 行为不变 —— 修 finance 闸不能殃及 employee。"""
    seed_user(db, email="employee@x.test", role="employee")
    headers = login(client, "employee@x.test")
    r = client.post(
        "/api/chat/sessions",
        headers=headers,
        json={"title": "employee session", "model": "gemini-3.5-flash"},
    )
    assert r.status_code == 200, r.text


# ─── Superadmin gate ───────────────────────────────────────────


def test_finance_user_cannot_access_superadmin_endpoint(client, db):
    """finance 用户访问 GET /api/users 应该 403 —— 不能改其他用户。

    为什么这是核心：把 finance 加进 require_writer 之后必须确认我**没有**误把
    finance 也加进 require_admin / require_superadmin。否则就是越权。"""
    seed_user(db, email="finance-jp@x.test", role="finance")
    headers = login(client, "finance-jp@x.test")
    r = client.get("/api/users", headers=headers)
    assert r.status_code == 403, r.text


def test_employee_user_also_cannot_access_superadmin_endpoint(client, db):
    """回归守护：employee 继续被 Superadmin 闸拒（一直如此，确认没改坏）。"""
    seed_user(db, email="employee@x.test", role="employee")
    headers = login(client, "employee@x.test")
    r = client.get("/api/users", headers=headers)
    assert r.status_code == 403, r.text
