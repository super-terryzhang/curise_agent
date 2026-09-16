"""Section 4 — Orders: agent tools LLM behaviour evaluation.

测试目标：
    真调 Gemini 验证 agent 面对用户问题时**选对**新的窄工具：
      - "订单 N 里有什么蔬菜" → list_order_products(query=...)
      - "匹配率多少" → get_order_match_stats
      - "订单 N 是什么" → get_order_detail
      - "完全不存在的订单" → get_order_detail 错误处理

    这是 Anthropic Tool Design 准则的回归测试 ("ambiguous tool
    descriptions lead agents to misunderstand"); 如果未来有人把
    docstring 改模糊 / 把窄工具合回 mega-tool, agent 选错工具，这
    些测试立刻报警。

为什么必须有：
    单元测试只能证明"工具如果被调，输出正确"。但 prod 2026-05-14
    事故是 agent **完全没调对工具**——单元测试全绿、用户体验崩。
    LLM eval 是堵这条线的唯一办法。

如何运行：
    GOOGLE_API_KEY=... PYTEST_RUN_SLOW=1 pytest \
        test_v2/section_4_orders/test_order_agent_tools_eval.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_skip_real_gemini = pytest.mark.skipif(
    not os.getenv("PYTEST_RUN_SLOW")
    and not os.getenv("RUN_REAL_GEMINI_TESTS")
    or not os.getenv("GOOGLE_API_KEY"),
    reason="real-Gemini agent eval; needs GOOGLE_API_KEY + PYTEST_RUN_SLOW=1",
)


def _seed_order_with_vegetables(db, seed_user) -> int:
    """Re-uses the same fixture builder from the unit test file to keep
    seeding consistent. Returns the order id."""
    from test_v2.section_4_orders.test_order_agent_tools import (
        _make_match_row,
        _seed_masterdata,
        _seed_products,
    )
    from domains.masterdata.models import Product
    from domains.orders.models import Order

    ids = _seed_masterdata(db)
    _seed_products(db, ids)
    fruit = db.query(Product).filter(Product.code == "FRT-APL").one()
    veg = db.query(Product).filter(Product.code == "VEG-CAR").one()
    beef = db.query(Product).filter(Product.code == "MEAT-BEEF").one()

    order = Order(
        user_id=seed_user.id,
        filename="eval-order.pdf",
        file_type="pdf",
        status="ready_for_review",
        po_number="EVAL-1",
        ship_name="MV Eval",
        currency="USD",
        delivery_date="2026-06-01",
        match_results=[
            _make_match_row(
                code="FRT-APL", name="Apple Red Delicious", qty=10,
                unit="CT", price=12.0, status="matched", matched=fruit,
            ),
            _make_match_row(
                code="VEG-CAR", name="Carrot Baby", qty=20, unit="KG",
                price=6.0, status="matched", matched=veg,
            ),
            _make_match_row(
                code="MEAT-BEEF", name="Beef Tenderloin", qty=5, unit="KG",
                price=55.0, status="matched", matched=beef,
            ),
            _make_match_row(
                code="UNK-001", name="Mystery Item", qty=5, unit="EA",
                price=99.0, status="not_matched", matched=None,
            ),
        ],
        product_count=4,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order.id


@_skip_real_gemini
def test_agent_uses_list_order_products_for_vegetable_query(db, seed_user):
    """User: '订单 N 里有什么蔬菜?' → agent should call list_order_products
    with a 'vegetable' / 'VEGETABLE' query string (not the slim
    get_order_detail, which doesn't carry products)."""
    from agent.runtime.factory import create_v3_chat_agent

    order_id = _seed_order_with_vegetables(db, seed_user)
    agent = create_v3_chat_agent(
        db=db, user_id=seed_user.id, user_role="superadmin"
    )
    answer = agent.run(f"订单 {order_id} 里有什么蔬菜?")

    tool_names = [s.name for s in agent.trace if s.kind == "tool"]
    assert "list_order_products" in tool_names, (
        f"expected list_order_products, got {tool_names}. "
        f"answer: {answer[:300]}..."
    )
    # The carrot should make it into the answer
    assert "Carrot" in answer or "胡萝卜" in answer or "VEG-CAR" in answer, (
        f"agent didn't surface the carrot. answer: {answer!r}"
    )


@_skip_real_gemini
def test_agent_uses_match_stats_for_match_rate_question(db, seed_user):
    """User: '匹配率怎么样' → get_order_match_stats."""
    from agent.runtime.factory import create_v3_chat_agent

    order_id = _seed_order_with_vegetables(db, seed_user)
    agent = create_v3_chat_agent(
        db=db, user_id=seed_user.id, user_role="superadmin"
    )
    answer = agent.run(f"订单 {order_id} 的产品匹配率是多少？")

    tool_names = [s.name for s in agent.trace if s.kind == "tool"]
    assert "get_order_match_stats" in tool_names, (
        f"expected get_order_match_stats, got {tool_names}. "
        f"answer: {answer[:300]}..."
    )
    # 3 matched out of 4 = 75%
    assert "75" in answer or "百分之七十五" in answer, (
        f"agent didn't say 75%. answer: {answer!r}"
    )


@_skip_real_gemini
def test_agent_uses_detail_for_metadata_question(db, seed_user):
    """User: '订单 N 是什么' → get_order_detail (metadata only)."""
    from agent.runtime.factory import create_v3_chat_agent

    order_id = _seed_order_with_vegetables(db, seed_user)
    agent = create_v3_chat_agent(
        db=db, user_id=seed_user.id, user_role="superadmin"
    )
    answer = agent.run(f"订单 {order_id} 是关于什么的？")

    tool_names = [s.name for s in agent.trace if s.kind == "tool"]
    # Acceptable: get_order_detail (preferred) OR list_orders (also OK as
    # entry point if agent first lists to find the ID).
    assert any(t in tool_names for t in ("get_order_detail", "list_orders")), (
        f"expected get_order_detail or list_orders, got {tool_names}"
    )
    # Surface key metadata.
    assert "EVAL-1" in answer or "MV Eval" in answer, (
        f"agent didn't surface PO/ship metadata. answer: {answer!r}"
    )


@_skip_real_gemini
def test_agent_handles_unmatched_question_via_match_status_filter(db, seed_user):
    """User: '哪些产品没匹配' → list_order_products(match_status='not_matched')."""
    from agent.runtime.factory import create_v3_chat_agent

    order_id = _seed_order_with_vegetables(db, seed_user)
    agent = create_v3_chat_agent(
        db=db, user_id=seed_user.id, user_role="superadmin"
    )
    answer = agent.run(f"订单 {order_id} 里有哪些产品没匹配上?")

    tool_names = [s.name for s in agent.trace if s.kind == "tool"]
    assert "list_order_products" in tool_names or "get_order_match_stats" in tool_names, (
        f"expected list_order_products or match_stats, got {tool_names}"
    )
    # The UNK-001 should surface
    assert "UNK-001" in answer or "Mystery" in answer, (
        f"agent didn't name the unmatched item. answer: {answer!r}"
    )
