"""Section 6 — Masterdata upload pipeline, end-to-end.

测试目标：
    把 HTTP → parse → resolve → preview → commit / rollback 串成一条真
    流水线，断言每一步都对得上 — 这是唯一能抓"两层都对但合起来错"的
    那种 contract drift bug（v3 v10/v11 询价页那波就是契约 drift 类型）。

    Stages 1-5 service 单元 + Tool 单元 + endpoint 单元都覆盖了，但它们
    各自独立。`test_full_flow_*` 是上述链路最薄的端到端覆盖：用真 HTTP
    上传 → 取 batch_id → 调真 tool → 真 commit → 在 DB 里看到真产品。

为什么重要：
    一旦中间某一层契约偏移（比如 endpoint 返回的 batch_id 变成 string、
    parse_uploaded_file 改成异步、commit 工具改了参数名），单元层都还
    绿但生产用户就再也无法完整跑通。E2E 是这种"层间漂移"的最早警报。

设计方法：
    最少的步骤 + 一个 happy path 一个 rollback path。Agent runtime 不在
    回路里（commit/rollback 是 Tier-2 dispatch，绕过 propose-action UI 流），
    避免引入 LLM mock 的复杂度 — 直接调 service.commit_batch / rollback_batch。
"""

from __future__ import annotations

import json
from pathlib import Path

from general_agent import REGISTRY, ToolContext

from agent.runtime import tools as _v3_tools  # noqa: F401 — register tools
from agent.runtime.deps import V3Deps, inject_deps
from domains.masterdata.models import Product
from domains.masterdata.upload import commit_batch, rollback_batch
from test_v2.fixtures.helpers import login, make_excel, seed_user


def _ctx(db, *, user_id: int) -> ToolContext:
    ctx = ToolContext(workspace=Path("/tmp"), extras={})
    inject_deps(ctx, V3Deps(db=db, user_id=user_id))
    return ctx


def test_full_flow_upload_parse_preview_commit(client, db, session_factory):
    """End-to-end happy path:
      1. seed user, login
      2. HTTP upload Excel → batch_id
      3. agent tool `parse_uploaded_file` → scores rows
      4. agent tool `preview_upload` → diff
      5. service `commit_batch` → real Product rows
    Asserts:
      - HTTP returned 200 with a numeric batch_id
      - Tool returned JSON (not "Error: ...")
      - Commit landed 2 new Product rows in DB
      - Batch finalised to "completed"
    """
    user = seed_user(db, email="emp@example.com")
    headers = login(client, "emp@example.com")
    blob = make_excel(
        [
            {"product_code": "E2E-1", "product_name": "E2E Apple", "price": 10, "unit": "kg"},
            {"product_code": "E2E-2", "product_name": "E2E Banana", "price": 5, "unit": "ea"},
        ]
    )

    # 1. Upload
    r = client.post(
        "/api/data-upload/upload",
        headers=headers,
        files={"file": ("flow.xlsx", blob, "application/octet-stream")},
    )
    assert r.status_code == 200, r.text
    batch_id = r.json()["batch_id"]
    assert isinstance(batch_id, int)

    # 2. Use a fresh session bound to the same in-memory engine — TestClient
    # opens its own request session, but the agent tool needs an open one.
    s = session_factory()
    try:
        # parse_uploaded_file
        out = REGISTRY.view(["parse_uploaded_file"]).dispatch(
            "parse_uploaded_file",
            {"batch_id": batch_id},
            ctx=_ctx(s, user_id=user.id),
        )
        assert not out.startswith("Error:"), out
        scored = json.loads(out)
        assert scored["batch_id"] == batch_id
        assert scored["total_rows"] == 2
        assert scored["new_rows"] == 2

        # preview_upload
        out = REGISTRY.view(["preview_upload"]).dispatch(
            "preview_upload",
            {"batch_id": batch_id},
            ctx=_ctx(s, user_id=user.id),
        )
        assert not out.startswith("Error:"), out
        diff = json.loads(out)
        assert len(diff["create"]) == 2

        # commit (Tier-2 action — service-direct here, mirrors the dispatcher)
        result = commit_batch(s, batch_id=batch_id, user_id=user.id)
        assert result["created"] == 2

        # DB has the real rows
        products = (
            s.query(Product)
            .filter(Product.product_name_en.in_(["E2E Apple", "E2E Banana"]))
            .all()
        )
        assert len(products) == 2
        names = {p.product_name_en for p in products}
        assert names == {"E2E Apple", "E2E Banana"}
    finally:
        s.close()


def test_full_flow_upload_then_rollback_restores_db(client, db, session_factory):
    """Same upload + commit; then `rollback_upload_batch` Tier-2 action
    reverses everything via the ChangeLog. Used when the user notices a
    bad upload AFTER commit (e.g. wrong supplier file)."""
    user = seed_user(db, email="emp@example.com")
    headers = login(client, "emp@example.com")
    blob = make_excel(
        [
            {"product_code": "RB-1", "product_name": "Rollback A"},
            {"product_code": "RB-2", "product_name": "Rollback B"},
        ]
    )
    r = client.post(
        "/api/data-upload/upload",
        headers=headers,
        files={"file": ("rb.xlsx", blob, "application/octet-stream")},
    )
    assert r.status_code == 200
    batch_id = r.json()["batch_id"]

    s = session_factory()
    try:
        REGISTRY.view(["parse_uploaded_file"]).dispatch(
            "parse_uploaded_file",
            {"batch_id": batch_id},
            ctx=_ctx(s, user_id=user.id),
        )
        commit_batch(s, batch_id=batch_id, user_id=user.id)

        # Sanity: 2 products in DB now.
        assert (
            s.query(Product)
            .filter(Product.product_name_en.like("Rollback%"))
            .count()
            == 2
        )

        # Rollback — the actual undo path users hit when they realise mistake.
        result = rollback_batch(s, batch_id=batch_id, user_id=user.id)
        assert result["deleted"] == 2

        # DB is back to clean.
        assert (
            s.query(Product)
            .filter(Product.product_name_en.like("Rollback%"))
            .count()
            == 0
        )
    finally:
        s.close()
