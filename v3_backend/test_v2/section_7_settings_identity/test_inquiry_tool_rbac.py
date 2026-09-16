"""Section 7 — Inquiry tool RBAC: order-ownership guard.

测试目标：
    `agent/runtime/tools/inquiry.py` 里的两个 tool — `generate_inquiry` 和
    `regenerate_supplier_inquiry` — 在调用 orchestrator 之前必须先验"调用人
    是否拥有这个 order"。
      - employee 试图操作别人的 order → 返回 `Error: ...` 字符串 (不抛异常)
      - admin / superadmin 可以越过 owner 检查 (业务上做 support / audit)
      - 错误是字符串协议: tool 不抛 Exception, 把 OrderError 翻成 "Error: ..."

为什么重要：
    chat agent 的 orchestrator 自己开 DB session 跑后台任务, 不带 user_id —
    所以 RBAC 必须在 tool 入口先把住。漏一行 = 任何 employee 让 chat agent
    "帮我生成 order 99 的询价" 都能跑别人订单, 等于把"生成 .xlsx 询价文件"
    这条副作用大的链路完全开放。

设计方法：
    用 V3Deps + ToolContext 模拟 chat 入口的注入路径 (跟
    test_v2/section_1_security/test_cross_user_isolation.py 同款)。
    用 monkeypatch 把 orchestrator.run_inquiry / run_inquiry_for_supplier 替成
    fake — 这样 admin 路径不会真跑 inquiry pipeline, 也不依赖 Gemini / 模板。
    跨用户测试根本不会走到 orchestrator (在 _ensure_order_owned 那一步就拒了),
    所以也不需要 mock。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from general_agent import REGISTRY, ToolContext

# Side-effect import: registers every v3 business tool on REGISTRY.
from agent.runtime import tools as _v3_tools  # noqa: F401
from agent.runtime.deps import V3Deps, inject_deps
from domains.inquiry.schemas import InquiryState
from domains.orders.models import Order
from test_v2.fixtures.helpers import seed_user


# ─── Helpers ──────────────────────────────────────────────────


def _ctx(db, *, user_id: int, role: str = "employee") -> ToolContext:
    """Build a ToolContext with V3Deps injected (same as chat HTTP factory)."""
    ctx = ToolContext(workspace=Path("/tmp"), extras={})
    inject_deps(ctx, V3Deps(db=db, user_id=user_id, user_role=role))
    return ctx


def _make_order(db, *, user_id: int, filename: str = "po.pdf") -> Order:
    o = Order(
        user_id=user_id,
        filename=filename,
        status="ready_for_review",
        order_metadata={},
        products=[],
        match_results=[],
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


def _fake_inquiry_state(order_id: int) -> InquiryState:
    """A minimal InquiryState — enough for `.to_legacy_dict()` not to crash."""
    return InquiryState(order_id=order_id, status="completed", supplier_count=0)


# ─── generate_inquiry: cross-user denial ──────────────────────


def test_generate_inquiry_employee_blocks_other_users_order(db) -> None:
    """employee 1 调 generate_inquiry(order_id=<owned by user 2>) → "Error: ...".
    必须在 _ensure_order_owned 那一步拒, 不能往下走 orchestrator。"""
    me = seed_user(db, email="alice@x.test", role="employee")
    other = seed_user(db, email="bob@x.test", role="employee")
    order = _make_order(db, user_id=other.id)

    out = REGISTRY.view(["generate_inquiry"]).dispatch(
        "generate_inquiry",
        {"order_id": order.id},
        ctx=_ctx(db, user_id=me.id, role="employee"),
    )
    assert isinstance(out, str)
    assert out.startswith("Error:"), f"必须以 'Error:' 开头, 实际: {out[:80]!r}"


def test_generate_inquiry_employee_can_run_on_own_order(db, monkeypatch) -> None:
    """对照组: employee 调 *自己的* order — 不该被 RBAC 拒。
    用 monkeypatch 替掉真 orchestrator, 测试只验"通过 RBAC 后能进 orchestrator"。"""
    me = seed_user(db, email="alice@x.test", role="employee")
    order = _make_order(db, user_id=me.id)

    from domains.inquiry import orchestrator

    called: dict[str, int] = {}

    def fake_run(order_id: int, **kw):
        called["order_id"] = order_id
        return _fake_inquiry_state(order_id)

    monkeypatch.setattr(orchestrator, "run_inquiry", fake_run)

    out = REGISTRY.view(["generate_inquiry"]).dispatch(
        "generate_inquiry",
        {"order_id": order.id},
        ctx=_ctx(db, user_id=me.id, role="employee"),
    )
    assert not out.startswith("Error:"), f"自家 order 被错杀: {out[:80]!r}"
    assert called.get("order_id") == order.id, "RBAC 通过后必须真调到 orchestrator"


def test_generate_inquiry_admin_bypasses_owner_check(db, monkeypatch) -> None:
    """admin 角色应该能跑 *任何* user 的 order — _ensure_order_owned 走 is_admin
    分支跳过 owner 过滤。"""
    admin = seed_user(db, email="ops@x.test", role="admin")
    victim = seed_user(db, email="user@x.test", role="employee")
    order = _make_order(db, user_id=victim.id, filename="theirs.pdf")

    from domains.inquiry import orchestrator

    called: dict[str, int] = {}

    def fake_run(order_id: int, **kw):
        called["order_id"] = order_id
        return _fake_inquiry_state(order_id)

    monkeypatch.setattr(orchestrator, "run_inquiry", fake_run)

    out = REGISTRY.view(["generate_inquiry"]).dispatch(
        "generate_inquiry",
        {"order_id": order.id},
        ctx=_ctx(db, user_id=admin.id, role="admin"),
    )
    assert not out.startswith("Error:")
    assert called.get("order_id") == order.id


def test_generate_inquiry_superadmin_also_bypasses(db, monkeypatch) -> None:
    """superadmin 跟 admin 同 tier — 一并要能跑别人 order, 否则 P0 support 受阻。"""
    su = seed_user(db, email="root@x.test", role="superadmin")
    victim = seed_user(db, email="user@x.test", role="employee")
    order = _make_order(db, user_id=victim.id)

    from domains.inquiry import orchestrator

    monkeypatch.setattr(
        orchestrator, "run_inquiry", lambda oid, **kw: _fake_inquiry_state(oid)
    )

    out = REGISTRY.view(["generate_inquiry"]).dispatch(
        "generate_inquiry",
        {"order_id": order.id},
        ctx=_ctx(db, user_id=su.id, role="superadmin"),
    )
    assert not out.startswith("Error:")


# ─── regenerate_supplier_inquiry: same matrix ─────────────────


def test_regenerate_supplier_inquiry_employee_blocks_other_users_order(db) -> None:
    """单供应商重跑路径必须复用同一 RBAC — 不然攻击面绕一圈又开了。"""
    me = seed_user(db, email="alice@x.test", role="employee")
    other = seed_user(db, email="bob@x.test", role="employee")
    order = _make_order(db, user_id=other.id)

    out = REGISTRY.view(["regenerate_supplier_inquiry"]).dispatch(
        "regenerate_supplier_inquiry",
        {"order_id": order.id, "supplier_id": 1},
        ctx=_ctx(db, user_id=me.id, role="employee"),
    )
    assert out.startswith("Error:")


def test_regenerate_supplier_inquiry_admin_bypasses_owner_check(db, monkeypatch) -> None:
    """admin 跑别人订单的 regenerate 也得放行。"""
    admin = seed_user(db, email="ops@x.test", role="admin")
    victim = seed_user(db, email="user@x.test", role="employee")
    order = _make_order(db, user_id=victim.id)

    from domains.inquiry import orchestrator

    called: dict[str, int] = {}

    def fake_run(order_id: int, supplier_id: int, **kw):
        called["order_id"] = order_id
        called["supplier_id"] = supplier_id
        return _fake_inquiry_state(order_id)

    monkeypatch.setattr(orchestrator, "run_inquiry_for_supplier", fake_run)

    out = REGISTRY.view(["regenerate_supplier_inquiry"]).dispatch(
        "regenerate_supplier_inquiry",
        {"order_id": order.id, "supplier_id": 42},
        ctx=_ctx(db, user_id=admin.id, role="admin"),
    )
    assert not out.startswith("Error:")
    assert called == {"order_id": order.id, "supplier_id": 42}


# ─── Error protocol: string, not exception ────────────────────


@pytest.mark.parametrize("tool_name,extra_args", [
    ("generate_inquiry", {}),
    ("regenerate_supplier_inquiry", {"supplier_id": 1}),
])
def test_rbac_violation_returns_error_string_not_exception(
    db, tool_name: str, extra_args: dict
) -> None:
    """跨用户拒绝必须是 *返回字符串* 而非 *抛 Exception* — agent tools 的统一协议。
    抛 Exception 会让 chat agent 整圈崩, 而 'Error: xxx' 走 happy path 由 LLM 处理。"""
    me = seed_user(db, email="alice@x.test", role="employee")
    other = seed_user(db, email="bob@x.test", role="employee")
    order = _make_order(db, user_id=other.id)

    args = {"order_id": order.id, **extra_args}
    out = REGISTRY.view([tool_name]).dispatch(
        tool_name, args, ctx=_ctx(db, user_id=me.id, role="employee")
    )
    # 关键断言: 不抛, 而是字符串
    assert isinstance(out, str)
    assert out.startswith("Error:")
