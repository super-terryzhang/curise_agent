"""Section 1 — Cross-user data isolation across every read/write surface.

测试目标：
    覆盖 v3 系统里 *每一个* user-scoped 入口都不能被另一个 user 通过 tool dispatch
    或 SessionStore 直接读到 / 写到别人数据：

      A. V3SessionStore (agent/runtime/session_store.py)
         - get(other_session)        → None
         - append(other_session, …)  → raises PermissionError
         - load(other_session)       → []
         - replace_all(other,…)      → raises PermissionError
      B. list_orders / get_order_detail
         - employee 只看到自己的 order
         - admin / superadmin 看到全部
         - employee 试图读别人的 order detail → Error
      C. list_documents / get_document
         - employee 只看到自己的 document
         - admin 看到全部
         - employee 试图 get 别人的 document → Error
      D. data_upload tools (parse_uploaded_file / preview_upload / list_my_uploads)
         - employee 试图操作别人的 batch → Error

为什么重要：
    这是整个多租户 SaaS 的根。任何一条 user_id 过滤的遗漏 = 数据泄露。
    我们之前出过的 K1/K2/K3 类 bug（agent 工具自己写 SQL 绕开 service）
    就是这一层的常见破洞 — 守门测试必须保持红/绿可见。

设计方法：
    每条 entry point 一个 test；ctx 由 V3Deps 注入；
    REGISTRY.view(["tool_name"]).dispatch() 直接走真实工具链路，
    返回字符串以 "Error:" 前缀代表拒绝（agent tools 的统一错误协议）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

# Side-effect import: registers every v3 business tool on REGISTRY.
from agent.runtime import tools as _v3_tools  # noqa: F401
from agent.runtime.deps import V3Deps, inject_deps
from agent.runtime.session_store import V3SessionStore
from domains.document.models import Document
from domains.identity.models import User
from domains.masterdata.upload import parse_excel
from domains.masterdata.upload.models import UploadBatch
from domains.orders.models import Order
from general_agent import REGISTRY, ToolContext
from infrastructure.security import hash_password
from test_v2.fixtures.helpers import make_excel

# ─── Test helpers ────────────────────────────────────────────


def _ctx_with_deps(db, *, user_id: int, role: str = "employee") -> ToolContext:
    """Build a ToolContext with V3Deps injected. Mirrors the chat-HTTP factory."""
    # The database is authoritative for roles; a ToolContext cannot grant roles.
    user = db.get(User, user_id)
    if user is None:
        user = User(id=user_id, email=f"synthetic-{user_id}@example.test", role=role,
                    hashed_password=hash_password("password123"), is_active=True, is_default_password=False)
        db.add(user)
    user.role = role
    db.commit()
    ctx = ToolContext(workspace=Path("/tmp"), extras={})
    inject_deps(ctx, V3Deps(db=db, user_id=user_id, user_role=role))
    return ctx


def _make_user(db, email: str, role: str = "employee") -> User:
    u = User(
        email=email,
        hashed_password=hash_password("password123"),
        full_name=email,
        role=role,
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _make_order(db, *, user_id: int, filename: str = "o.pdf", **fields) -> Order:
    o = Order(
        user_id=user_id,
        filename=filename,
        status=fields.get("status", "ready_for_review"),
        order_metadata=fields.get("order_metadata", {}),
        products=fields.get("products", []),
        match_results=fields.get("match_results", []),
        po_number=fields.get("po_number"),
        ship_name=fields.get("ship_name"),
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


def _make_doc(
    db,
    *,
    user_id: int,
    filename: str = "f.pdf",
    file_type: str = "pdf",
    doc_type: str = "purchase_order",
    status: str = "extracted",
) -> Document:
    d = Document(
        user_id=user_id,
        filename=filename,
        file_url=None,
        file_type=file_type,
        file_size_bytes=0,
        doc_type=doc_type,
        content_markdown="",
        extracted_data=None,
        status=status,
        created_at=datetime.utcnow(),
        extracted_at=datetime.utcnow() if status == "extracted" else None,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def _make_batch(db, *, user_id: int) -> UploadBatch:
    blob = make_excel([{"product_name": "Test Product"}])
    return parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=user_id)


# ─── A. V3SessionStore: storage-layer isolation ──────────────


def test_session_store_get_returns_none_for_other_user(db) -> None:
    """A session belongs to ONE user. A store bound to user B must NOT see
    user A's session even when they pass the right session_id."""
    store_a = V3SessionStore(db, user_id=1)
    store_b = V3SessionStore(db, user_id=2)
    sid = store_a.create(title="alice's chat")
    assert store_a.get(sid) is not None
    assert store_b.get(sid) is None


def test_session_store_load_returns_empty_for_other_user(db) -> None:
    """Even if user B knows the session id, `load` must yield no messages."""
    store_a = V3SessionStore(db, user_id=1)
    store_b = V3SessionStore(db, user_id=2)
    sid = store_a.create()
    store_a.append(sid, {"role": "user", "content": "hello"})
    assert store_a.load(sid) != []
    assert store_b.load(sid) == []


def test_session_store_append_raises_permission_error_cross_user(db) -> None:
    """Cross-user write must fail LOUDLY — a silent skip would still corrupt
    audit logs and break the agent's idea of the conversation."""
    store_a = V3SessionStore(db, user_id=1)
    store_b = V3SessionStore(db, user_id=2)
    sid = store_a.create()
    with pytest.raises(PermissionError):
        store_b.append(sid, {"role": "user", "content": "steal"})


def test_session_store_replace_all_raises_permission_error_cross_user(db) -> None:
    """replace_all rewrites entire history — equally dangerous as append.
    Must also raise PermissionError when not the owner."""
    store_a = V3SessionStore(db, user_id=1)
    store_b = V3SessionStore(db, user_id=2)
    sid = store_a.create()
    with pytest.raises(PermissionError):
        store_b.replace_all(sid, [{"role": "user", "content": "nuke"}])


def test_session_store_list_sessions_filters_by_user(db) -> None:
    """Each user's session list shows only their own — no cross-contamination."""
    store_a = V3SessionStore(db, user_id=1)
    store_b = V3SessionStore(db, user_id=2)
    sid_a = store_a.create(title="alice")
    sid_b = store_b.create(title="bob")
    sessions_a = {s["id"] for s in store_a.list_sessions()}
    sessions_b = {s["id"] for s in store_b.list_sessions()}
    assert sid_a in sessions_a
    assert sid_b not in sessions_a
    assert sid_b in sessions_b
    assert sid_a not in sessions_b


# ─── B. list_orders: employee scoped, admin sees all ─────────


def test_list_orders_employee_sees_only_own_orders(db, seed_user) -> None:
    """employee role → SQL filter `Order.user_id == <self>` kicks in."""
    other = _make_user(db, "other@x.test")
    _make_order(db, user_id=seed_user.id, filename="mine.pdf")
    _make_order(db, user_id=other.id, filename="theirs.pdf")
    out = REGISTRY.view(["list_orders"]).dispatch(
        "list_orders", {}, ctx=_ctx_with_deps(db, user_id=seed_user.id, role="employee")
    )
    payload = json.loads(out)
    filenames = {item["filename"] for item in payload["items"]}
    assert "mine.pdf" in filenames
    assert "theirs.pdf" not in filenames


def test_list_orders_admin_sees_all_orders(db, seed_user) -> None:
    """admin role → `include_all_users=True` → no user_id filter."""
    other = _make_user(db, "other@x.test")
    _make_order(db, user_id=seed_user.id, filename="mine.pdf")
    _make_order(db, user_id=other.id, filename="theirs.pdf")
    out = REGISTRY.view(["list_orders"]).dispatch(
        "list_orders", {}, ctx=_ctx_with_deps(db, user_id=seed_user.id, role="admin")
    )
    filenames = {item["filename"] for item in json.loads(out)["items"]}
    assert {"mine.pdf", "theirs.pdf"}.issubset(filenames)


def test_list_orders_superadmin_sees_all_orders(db, seed_user) -> None:
    """superadmin must be treated like admin (also in role check)."""
    other = _make_user(db, "other@x.test")
    _make_order(db, user_id=seed_user.id, filename="mine.pdf")
    _make_order(db, user_id=other.id, filename="theirs.pdf")
    out = REGISTRY.view(["list_orders"]).dispatch(
        "list_orders", {}, ctx=_ctx_with_deps(db, user_id=seed_user.id, role="superadmin")
    )
    filenames = {item["filename"] for item in json.loads(out)["items"]}
    assert {"mine.pdf", "theirs.pdf"}.issubset(filenames)


# ─── B'. get_order_detail blocks cross-user reads ────────────


def test_get_order_detail_employee_blocks_other_user_order(db, seed_user) -> None:
    """employee fetching someone else's order id → service raises NotFound,
    tool catches OrderError and returns an Error string. Must NOT leak the row."""
    other = _make_user(db, "other@x.test")
    o = _make_order(db, user_id=other.id, filename="theirs.pdf")
    out = REGISTRY.view(["get_order_detail"]).dispatch(
        "get_order_detail",
        {"order_id": o.id},
        ctx=_ctx_with_deps(db, user_id=seed_user.id, role="employee"),
    )
    assert out.startswith("Error:")
    # Sanity: the other user's filename must not be in the response.
    assert "theirs.pdf" not in out


def test_get_order_detail_admin_can_read_any_order(db, seed_user) -> None:
    """Admin must be able to inspect any user's order (for support / audit)."""
    other = _make_user(db, "other@x.test")
    o = _make_order(db, user_id=other.id, filename="theirs.pdf")
    out = REGISTRY.view(["get_order_detail"]).dispatch(
        "get_order_detail",
        {"order_id": o.id},
        ctx=_ctx_with_deps(db, user_id=seed_user.id, role="admin"),
    )
    assert not out.startswith("Error:")
    body = json.loads(out)
    assert body["filename"] == "theirs.pdf"


# ─── C. list_documents: employee scoped, admin sees all ──────


def test_list_documents_employee_sees_all_company_wide(db) -> None:
    """2026-07-03 policy flip: documents are company-wide, not per-user.
    Employees now see ALL documents in list_documents — the shared
    inbox for the whole team.

    Locks in the "everyone sees everything" contract; if a future
    refactor re-adds a user_id filter for employees, this fails.
    """
    _make_doc(db, user_id=1, filename="mine.pdf")
    _make_doc(db, user_id=2, filename="theirs.pdf")
    out = REGISTRY.view(["list_documents"]).dispatch(
        "list_documents", {}, ctx=_ctx_with_deps(db, user_id=1, role="employee")
    )
    body = json.loads(out)
    filenames = {item["filename"] for item in body["items"]}
    assert "mine.pdf" in filenames
    assert "theirs.pdf" in filenames  # ← the new behavior


def test_list_documents_admin_sees_all(db) -> None:
    """admin role → no user_id filter; returns every document."""
    _make_doc(db, user_id=1, filename="a.pdf")
    _make_doc(db, user_id=2, filename="b.pdf")
    out = REGISTRY.view(["list_documents"]).dispatch(
        "list_documents", {}, ctx=_ctx_with_deps(db, user_id=1, role="admin")
    )
    filenames = {item["filename"] for item in json.loads(out)["items"]}
    assert {"a.pdf", "b.pdf"}.issubset(filenames)


def test_get_document_employee_can_read_any_company_wide(db) -> None:
    """2026-07-03: `get_document` on someone else's upload succeeds —
    company-wide read. Destructive ops (delete / doc_type change /
    reextract) still gate by uploader, tested in
    test_destructive_ops_still_owner_only below."""
    d = _make_doc(db, user_id=99, filename="not-yours.pdf")
    out = REGISTRY.view(["get_document"]).dispatch(
        "get_document",
        {"document_id": d.id},
        ctx=_ctx_with_deps(db, user_id=1, role="employee"),
    )
    assert not out.startswith("Error:"), (
        f"read should succeed for any employee (company-wide); got: {out}"
    )
    body = json.loads(out)
    assert body["filename"] == "not-yours.pdf"


def test_get_document_admin_can_read_any(db) -> None:
    """Admin must be able to inspect any user's document."""
    d = _make_doc(db, user_id=99, filename="theirs.pdf")
    out = REGISTRY.view(["get_document"]).dispatch(
        "get_document",
        {"document_id": d.id},
        ctx=_ctx_with_deps(db, user_id=1, role="admin"),
    )
    assert not out.startswith("Error:")
    body = json.loads(out)
    assert body["filename"] == "theirs.pdf"


# ─── D. data-upload tools: per-user batch isolation ──────────


def test_parse_uploaded_file_blocks_other_users_batch(db) -> None:
    """User 1 cannot resolve user 2's batch — upload_service.resolve_and_score
    checks ownership before doing any work."""
    batch = _make_batch(db, user_id=2)
    out = REGISTRY.view(["parse_uploaded_file"]).dispatch(
        "parse_uploaded_file",
        {"batch_id": batch.id},
        ctx=_ctx_with_deps(db, user_id=1, role="employee"),
    )
    assert out.startswith("Error:")


def test_preview_upload_blocks_other_users_batch(db) -> None:
    """preview_changes must also enforce ownership."""
    batch = _make_batch(db, user_id=2)
    out = REGISTRY.view(["preview_upload"]).dispatch(
        "preview_upload",
        {"batch_id": batch.id},
        ctx=_ctx_with_deps(db, user_id=1, role="employee"),
    )
    assert out.startswith("Error:")


def test_list_my_uploads_filters_by_user(db) -> None:
    """list_my_uploads must only return the caller's batches even when the DB
    holds batches for several users."""
    _make_batch(db, user_id=1)
    _make_batch(db, user_id=2)
    out = REGISTRY.view(["list_my_uploads"]).dispatch(
        "list_my_uploads", {}, ctx=_ctx_with_deps(db, user_id=1, role="employee")
    )
    body = json.loads(out)
    # user 1 has 1 batch, user 2's batch must not show up
    assert body["total_matching"] == 1
    assert body["returned"] == 1
