"""P9 — Proactive `remember` capture rate.

测试目标：
    用户**没说"记住"**两个字，只是陈述了一个**可持续偏好**或**业务事实**
    时，agent 应该主动调 `remember` 把它存进 V3Memory。Phase 1 prompt
    强化后预期可靠性提升。

    同时反向测试：一个**一次性查询**（"订单 42 的产品"）agent 必须
    **不**调 remember —— 否则记忆库会被垃圾查询塞爆。

测试矩阵：
    正向 1: "我们公司发票邮箱是 invoice@cruise.example.com" → 应存
    正向 2: "默认运费按 USD 计价" → 应存
    反向:   "订单 42 包含哪些产品" → 不应存

预期可靠性（Phase 1 baseline）：
    正向 ~50-70%（Mem0 论文 baseline 数字）
    反向 ~95%+（拒绝存查询应该好做）

为什么这个测试有价值：
    Phase 1 用 prompt + 例子提高 capture rate。这个测试就是 capture
    rate 的 ruler。将来 Phase 2 上 LLM-extraction，预期数字直接跃到
    85%+，跑同一组测试就能横向对比。
"""

from __future__ import annotations

import pytest

from test_v2.section_10_agent_reliability.conftest import (
    PROVIDER_PARAMS,
    record_trial,
    run_real_agent,
    skip_reliability,
)


# ─── Positive cases: agent SHOULD remember ─────────────────


@skip_reliability
@pytest.mark.parametrize("model", PROVIDER_PARAMS)
@pytest.mark.parametrize("trial", [0, 1, 2])
def test_invoice_email_durable_fact(db, seed_user, real_llm_enabled, model, trial):
    """陈述发票邮箱（durable business fact） → agent 应调 remember."""
    from agent.storage.models import AgentMemory

    BUCKET = "P9a-remember-invoice-email"
    EMAIL = "invoice@cruise.example.com"
    error = None
    run = None
    passed = False
    failure_detail = ""
    try:
        run, _ = run_real_agent(
            db,
            seed_user.id,
            f"我们公司发票邮箱是 {EMAIL}，以后处理订单时记得用这个邮箱。",
            model=model,
        )
        called_remember = "remember" in run.tool_names
        mem_rows = (
            db.query(AgentMemory).filter(AgentMemory.user_id == seed_user.id).all()
        )
        persisted = any(EMAIL in (r.value or "") for r in mem_rows)

        if not called_remember:
            failure_detail += f"agent did NOT call remember. tools={run.tool_names}\n"
        if not persisted:
            failure_detail += (
                f"AgentMemory has no row containing {EMAIL!r}. "
                f"rows={[(r.memory_type, r.value[:60]) for r in mem_rows]}\n"
            )
        passed = called_remember and persisted
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"

    record_trial(BUCKET, model, trial, passed=passed, run=run, error=error)
    if error:
        pytest.fail(f"agent raised: {error}")
    assert passed, failure_detail


@skip_reliability
@pytest.mark.parametrize("model", PROVIDER_PARAMS)
@pytest.mark.parametrize("trial", [0, 1, 2])
def test_currency_preference(db, seed_user, real_llm_enabled, model, trial):
    """陈述货币偏好（user preference） → agent 应调 remember."""
    from agent.storage.models import AgentMemory

    BUCKET = "P9b-remember-currency-pref"
    error = None
    run = None
    passed = False
    failure_detail = ""
    try:
        run, _ = run_real_agent(
            db,
            seed_user.id,
            "我们默认所有报价都按 USD 计价，以后查询、生成询价单都用美元。",
            model=model,
        )
        called_remember = "remember" in run.tool_names
        mem_rows = (
            db.query(AgentMemory).filter(AgentMemory.user_id == seed_user.id).all()
        )
        # Match either USD, "美元", or "美金"
        persisted = any(
            ("USD" in (r.value or "") or "美元" in (r.value or "") or "美金" in (r.value or ""))
            for r in mem_rows
        )

        if not called_remember:
            failure_detail += f"agent did NOT call remember. tools={run.tool_names}\n"
        if not persisted:
            failure_detail += (
                f"AgentMemory has no USD preference row. "
                f"rows={[(r.memory_type, r.value[:60]) for r in mem_rows]}\n"
            )
        passed = called_remember and persisted
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"

    record_trial(BUCKET, model, trial, passed=passed, run=run, error=error)
    if error:
        pytest.fail(f"agent raised: {error}")
    assert passed, failure_detail


# ─── Negative case: agent must NOT remember a one-off query ───


def _seed_one_order(db, seed_user) -> int:
    from test_v2.section_4_orders.test_order_agent_tools import (
        _make_match_row,
        _seed_masterdata,
        _seed_products,
    )
    from domains.masterdata.models import Product
    from domains.orders.models import Order

    ids = _seed_masterdata(db)
    _seed_products(db, ids)
    veg = db.query(Product).filter(Product.code == "VEG-CAR").one()
    order = Order(
        user_id=seed_user.id,
        filename="p9-target.pdf",
        file_type="pdf",
        status="ready_for_review",
        po_number="PO-P9",
        ship_name="MV P9",
        currency="USD",
        delivery_date="2026-06-01",
        match_results=[
            _make_match_row(
                code="X", name="X", qty=1, unit="EA", price=1.0,
                status="matched", matched=veg,
            ),
        ],
        product_count=1,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order.id


@skip_reliability
@pytest.mark.parametrize("model", PROVIDER_PARAMS)
@pytest.mark.parametrize("trial", [0, 1, 2])
def test_one_off_query_should_not_remember(
    db, seed_user, real_llm_enabled, model, trial
):
    """用户问一个**一次性查询**，agent 不应该把它当作 durable fact 存."""
    from agent.storage.models import AgentMemory

    BUCKET = "P9c-no-remember-on-query"
    order_id = _seed_one_order(db, seed_user)

    error = None
    run = None
    passed = False
    failure_detail = ""
    try:
        run, _ = run_real_agent(
            db,
            seed_user.id,
            f"订单 {order_id} 里有哪些产品？",
            model=model,
        )
        called_remember = "remember" in run.tool_names
        mem_rows = (
            db.query(AgentMemory).filter(AgentMemory.user_id == seed_user.id).all()
        )

        if called_remember:
            failure_detail += (
                f"agent SHOULDN'T have called remember for a one-off query. "
                f"tools={run.tool_names} mem_rows={len(mem_rows)}\n"
            )
        if mem_rows:
            failure_detail += (
                f"AgentMemory should be empty after a one-off query, got "
                f"{[(r.memory_type, r.value[:60]) for r in mem_rows]}\n"
            )
        passed = (not called_remember) and (len(mem_rows) == 0)
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"

    record_trial(BUCKET, model, trial, passed=passed, run=run, error=error)
    if error:
        pytest.fail(f"agent raised: {error}")
    assert passed, failure_detail
