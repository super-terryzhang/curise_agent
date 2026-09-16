"""P2 — Bucket B: long-horizon same-session, ≥3 dependent turns.

测试目标：
    Agent 在**同一 session_id** 下连续 3 轮，每轮引用上一轮的结果。
    考的是：
      1. V3SessionStore 正确把 Turn N-1 的 tool_calls + tool_results
         灌回 Turn N 的 messages
      2. LLM 能解析"那一单"、"它"、"这些"之类的代词回指
      3. agent.trace 在第 N 轮不污染（每轮独立）

剧本：
    T1: "列出 ship_name 含 'MV Match' 的订单"
        → expected: list_orders → 2 个订单
    T2: "其中产品数量最多的是哪一单？"
        → expected: get_order_detail or list_order_products on each
        → answer: 最多产品的那一单的 PO/ship 名
    T3: "把那一单里没匹配上的产品列出来"
        → expected: list_order_products(order_id=..., match_status=not_matched)
        → answer: 包含 unmatched item code/name

为什么这个 bucket：
    Prod 真实交互几乎都是多轮：用户先问什么、再追问。如果 Turn 2
    每次都得重报一遍上下文，UX 就崩了。

种子：
    2 个 ship_name='MV Match-X' 的订单：
      MV Match-A: 3 个产品（2 matched, 1 not_matched, name="Lost Item"）
      MV Match-B: 7 个产品（6 matched, 1 not_matched, name="Phantom Goods")
"""

from __future__ import annotations

import re

import pytest

from test_v2.section_10_agent_reliability.conftest import (
    PROVIDER_PARAMS,
    record_trial,
    run_real_agent,
    skip_reliability,
)


def _seed_two_matched_ships(db, seed_user) -> dict[str, int]:
    """Return {"small": id_A, "large": id_B}."""
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

    # MV Match-A — 3 products, 1 unmatched named "Lost Item"
    order_a = Order(
        user_id=seed_user.id,
        filename="match-a.pdf",
        file_type="pdf",
        status="ready_for_review",
        po_number="PO-MA-001",
        ship_name="MV Match-A",
        currency="USD",
        delivery_date="2026-06-01",
        match_results=[
            _make_match_row(code="A1", name="OK A1", qty=1, unit="EA", price=1.0, status="matched", matched=veg),
            _make_match_row(code="A2", name="OK A2", qty=1, unit="EA", price=1.0, status="matched", matched=veg),
            _make_match_row(code="LOST-A", name="Lost Item", qty=9, unit="EA", price=99.0, status="not_matched", matched=None),
        ],
        product_count=3,
    )
    db.add(order_a)
    db.commit()
    db.refresh(order_a)

    # MV Match-B — 7 products, 1 unmatched named "Phantom Goods"
    rows_b = [
        _make_match_row(code=f"B{i}", name=f"OK B{i}", qty=1, unit="EA", price=1.0, status="matched", matched=veg)
        for i in range(6)
    ]
    rows_b.append(
        _make_match_row(code="PHANTOM-B", name="Phantom Goods", qty=4, unit="EA", price=44.0, status="not_matched", matched=None)
    )
    order_b = Order(
        user_id=seed_user.id,
        filename="match-b.pdf",
        file_type="pdf",
        status="ready_for_review",
        po_number="PO-MB-002",
        ship_name="MV Match-B",
        currency="USD",
        delivery_date="2026-06-01",
        match_results=rows_b,
        product_count=7,
    )
    db.add(order_b)
    db.commit()
    db.refresh(order_b)

    return {"small": order_a.id, "large": order_b.id}


@skip_reliability
@pytest.mark.parametrize("model", PROVIDER_PARAMS)
@pytest.mark.parametrize("trial", [0, 1, 2])
def test_three_turn_referential_chain(db, seed_user, real_llm_enabled, model, trial):
    """T1 list → T2 reference → T3 reference-of-reference. Same session_id."""
    BUCKET = "P2-long-horizon-3turn"

    ids = _seed_two_matched_ships(db, seed_user)

    error = None
    run1 = run2 = run3 = None
    passed = False
    failure_detail = ""
    try:
        # ─── Turn 1 ────────────────────────────────────
        run1, agent1 = run_real_agent(
            db,
            seed_user.id,
            "请列出船名包含 'MV Match' 的订单。",
            model=model,
        )
        sid = agent1.session_id

        # Soft check: T1 must mention both MV Match-A and MV Match-B
        t1_mentions_both = ("MV Match-A" in run1.answer) and ("MV Match-B" in run1.answer)
        if not t1_mentions_both:
            failure_detail = (
                f"T1 didn't list both ships. answer={run1.answer!r}"
            )

        # ─── Turn 2 ────────────────────────────────────
        run2, _ = run_real_agent(
            db,
            seed_user.id,
            "这两单里，哪一单的产品数量更多？",
            model=model,
            session_id=sid,
        )
        # T2 must conclude the larger order. Accept any identifier the
        # agent picks: ship name, PO number, or DB id with arbitrary
        # spacing (Gemini sometimes writes "订单 ID 为 2", sometimes
        # "订单ID为2" — both mean the same thing).
        large_id = ids["large"]
        # Match "订单" possibly followed by spaces/ID/whatever then the
        # digit, OR "order 2", OR ship/PO. Stop at any non-digit boundary.
        id_pattern = re.compile(
            rf"(订单\s*(?:id|ID)?\s*[为是:：]?\s*{large_id}\b|order\s*{large_id}\b)",
            re.IGNORECASE,
        )
        t2_correct = (
            "MV Match-B" in run2.answer
            or "PO-MB-002" in run2.answer
            or bool(id_pattern.search(run2.answer))
        )
        if not t2_correct:
            failure_detail += (
                f"\nT2 didn't pick MV Match-B (id={large_id}). "
                f"answer={run2.answer!r}"
            )

        # ─── Turn 3 ────────────────────────────────────
        run3, _ = run_real_agent(
            db,
            seed_user.id,
            "把那一单里没匹配上的产品列出来，给出商品代码或名称。",
            model=model,
            session_id=sid,
        )
        # T3 must surface "PHANTOM-B" code OR "Phantom Goods" name
        t3_correct = (
            "PHANTOM-B" in run3.answer
            or "Phantom Goods" in run3.answer.lower() or "phantom" in run3.answer.lower()
        )
        if not t3_correct:
            failure_detail += f"\nT3 didn't surface phantom item. answer={run3.answer!r}"

        passed = t1_mentions_both and t2_correct and t3_correct
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"

    # Use run3 as the "score" for reporting (final turn is what user sees)
    record_trial(BUCKET, model, trial, passed=passed, run=run3, error=error)

    if error:
        pytest.fail(f"agent raised: {error}")
    assert passed, failure_detail or "see report"
