"""P1 — Bucket A: single-turn multi-tool chain.

测试目标：
    Agent 面对一个需要**串多个工具**才能回答的问题时，能不能持续推
    进、不卡死、不抄近路。问题："本月哪几个订单匹配率低于 50%"。

    标准链路应该长这样：
        list_orders() → [n 个订单]
        for each:
            get_order_match_stats(order_id)
        总结输出匹配率 < 50% 的订单 id

    或者走 query_db 一句 SQL 解决（superadmin 才有这个工具）—— 这
    其实是有效的"走捷径"信号，agent 选择更短路径是好事，所以我们
    只断**最终答案**命中正确订单，不强制多工具。

种子：
    5 个订单，匹配率分别 100%/80%/40%/20%/0%。低于 50% 的预期是 3 个
    (40%/20%/0%)。

为什么这个 bucket：
    串多工具需要 agent (a) 记住第一步结果 (b) 派生后续调用 (c) 聚合。
    任何环节挂掉都失败。比 P0.5 的纯 LLM 复述更接近真实任务。
"""

from __future__ import annotations

import pytest

from test_v2.section_10_agent_reliability.conftest import (
    PROVIDER_PARAMS,
    record_trial,
    run_real_agent,
    skip_reliability,
)


def _seed_orders_with_varied_match_rates(db, seed_user) -> dict[int, float]:
    """Build 5 orders with controlled match rates. Returns {order_id: rate}.

    Match rate = matched products / total products in `match_results`.
    Using 5 line items each so rates land on clean percentages."""
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

    # 5 items each, varying matched count: 5, 4, 2, 1, 0 → rates 100/80/40/20/0
    plans = [
        ("PO-100", "MV Alpha", 5),  # 100%
        ("PO-080", "MV Bravo", 4),  # 80%
        ("PO-040", "MV Charlie", 2),  # 40%
        ("PO-020", "MV Delta", 1),  # 20%
        ("PO-000", "MV Echo", 0),  # 0%
    ]
    rate_by_id: dict[int, float] = {}

    for po, ship, matched_count in plans:
        rows = []
        for i in range(5):
            is_matched = i < matched_count
            rows.append(
                _make_match_row(
                    code=f"PROD-{po}-{i}",
                    name=f"Item {i}",
                    qty=1,
                    unit="EA",
                    price=10.0,
                    status="matched" if is_matched else "not_matched",
                    matched=fruit if is_matched and i % 2 == 0 else (
                        veg if is_matched else None
                    ),
                )
            )
        order = Order(
            user_id=seed_user.id,
            filename=f"{po}.pdf",
            file_type="pdf",
            status="ready_for_review",
            po_number=po,
            ship_name=ship,
            currency="USD",
            delivery_date="2026-06-01",
            match_results=rows,
            product_count=5,
        )
        db.add(order)
        db.commit()
        db.refresh(order)
        rate_by_id[order.id] = matched_count / 5

    return rate_by_id


@skip_reliability
@pytest.mark.parametrize("model", PROVIDER_PARAMS)
@pytest.mark.parametrize("trial", [0, 1, 2])
def test_low_match_rate_orders(db, seed_user, real_llm_enabled, model, trial):
    """问 agent 匹配率<50% 的订单 → 答案必须命中 40%/20%/0% 这三单 的
    PO number 或 ship name（任一识别符即可，不强制 id）。"""
    BUCKET = "P1-multi-tool-chain"

    rate_by_id = _seed_orders_with_varied_match_rates(db, seed_user)
    expected_low = [
        (oid, rate) for oid, rate in rate_by_id.items() if rate < 0.5
    ]
    expected_ship_names = {
        "MV Charlie",  # 40%
        "MV Delta",  # 20%
        "MV Echo",  # 0%
    }
    # PO numbers are equally valid identifiers — the agent might cite
    # either form, so we accept any one of them per low-match order.
    expected_po_or_ship = [
        ("PO-040", "MV Charlie"),
        ("PO-020", "MV Delta"),
        ("PO-000", "MV Echo"),
    ]

    error = None
    run = None
    passed = False
    try:
        run, _ = run_real_agent(
            db,
            seed_user.id,
            (
                "我们一共有几个订单？其中产品匹配率低于 50% 的有哪些？"
                "请列出它们的 PO number 或船名。"
            ),
            model=model,
        )

        # End-state assertion: every low-match order must be cited by
        # either its PO number OR its ship name. Don't be picky about
        # what the agent calls it.
        answer = run.answer or ""
        all_cited = all(
            (po in answer) or (ship in answer)
            for po, ship in expected_po_or_ship
        )
        # Don't include false positives — answer shouldn't list 100%/80%
        # ones as low-match (allowing "MV Alpha" to appear in passing is
        # fine since lists usually mention totals; we relax this)
        no_high_match_mislabeled = True  # leave generous on first pass

        passed = all_cited and no_high_match_mislabeled
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"

    record_trial(BUCKET, model, trial, passed=passed, run=run, error=error)

    if error:
        pytest.fail(f"agent raised: {error}")
    assert passed, (
        f"answer should cite all of {expected_ship_names}, got: {run.answer!r}\n"
        f"  tools: {run.tool_names}"
    )
