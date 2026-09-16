"""P0.5 — Cross-turn context smoke test (Bucket B's go/no-go gate).

测试目标：
    最低限度验证「同一 session_id 跑两次 agent.run，第二次能看到第一次
    的 user message」。如果这都跑不通，Bucket B 整个长链路 scenario
    全是空中楼阁。

测试方法：
    T1 user 说一个孤立事实（"我的名字是张三"）
    T2 user 问 "我叫什么名字"
    断言 T2 的答案里包含 "张三"

    不依赖 remember/recall 工具 —— 纯靠 LLM 把 T1 的消息从 session
    history 读回来。这测的是 V3SessionStore 的往返完整性 + 模型对
    history 的基础理解，不是任何"工具能力"。

为什么放在 P0.5：
    如果失败 → Bucket B 砍掉；只跑单轮 buckets。
    如果通过 → 后面所有跨轮 scenario 都有基础保证。
"""

from __future__ import annotations

import pytest

from test_v2.section_10_agent_reliability.conftest import (
    PROVIDER_PARAMS,
    record_trial,
    run_real_agent,
    skip_reliability,
)


@skip_reliability
@pytest.mark.parametrize("model", PROVIDER_PARAMS)
@pytest.mark.parametrize("trial", [0, 1, 2])
def test_two_turn_name_recall(db, seed_user, real_llm_enabled, model, trial):
    """T1: 给 agent 一个事实。T2: 问 agent 这个事实。

    断言：T2 的答案 contains "张三"。否定结果意味着 cross-turn history
    回放有问题（或模型不行）。"""
    BUCKET = "P0.5-2turn-recall"
    NAME = "张三"

    error = None
    run1 = None
    run2 = None
    passed = False
    try:
        # ─── Turn 1: 灌入事实 ──────────────────────────
        run1, agent1 = run_real_agent(
            db,
            seed_user.id,
            f"请注意：我叫{NAME}。",
            model=model,
        )
        # 必须复用 session_id 才能跨轮
        sid = agent1.session_id

        # ─── Turn 2: 问回来 ────────────────────────────
        run2, _ = run_real_agent(
            db,
            seed_user.id,
            "我的名字是什么？请直接回答名字本身，不要调用工具。",
            model=model,
            session_id=sid,
        )

        passed = NAME in (run2.answer or "")
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"

    # Record trial for the report — use run2 (the turn we're scoring)
    record_trial(
        BUCKET,
        model,
        trial,
        passed=passed,
        run=run2,
        error=error,
    )

    if error:
        pytest.fail(f"agent raised: {error}")
    assert passed, (
        f"Turn 2 answer should contain {NAME!r}, got: {run2.answer!r}\n"
        f"  Turn 1 tools: {run1.tool_names if run1 else '?'}\n"
        f"  Turn 2 tools: {run2.tool_names}"
    )
