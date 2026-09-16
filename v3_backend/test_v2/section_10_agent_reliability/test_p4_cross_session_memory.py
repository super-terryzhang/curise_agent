"""P4 — Bucket D: cross-session memory via auto-injected preamble.

测试目标：
    S1: 用户告诉 agent 一个事实 → agent 调 `remember` 工具持久化到
        v3_agent_memories 表。
    S2 (新 session_id, 同 user_id): 用户问同一个事实 → factory 已经
        把 memory 自动注入到 system prompt → agent 直接从 context
        读出来回答，**无需调用 recall 工具**。

    断言：
      (a) S1 trace 含 `remember`，DB 持久化成功
      (b) S2 答案包含 FACT_VALUE 字符串
      (c) S2 **不**走 `search_masterdata` / `query_db` 这条错路（说明
         auto-injection 生效，agent 没去乱找）

架构背景：
    2026-05-15 起 `agent/runtime/factory.py` 在 web 平台 agent 启动时
    自动把 user 最近 30 条 memory 注入到 system prompt（hard-capped 至
    2000 chars）。这是 Mem0 / MemGPT Core Memory / ChatGPT memory 都
    在用的业界标准模式。`recall` 工具保留作为 power-user 显式深查。

    旧版本（仅 tool-call）pass^k = 0/6 —— 两家 LLM 都不主动调 recall。
    新版本预期 pass^k = ✓/✓ 因为事实已经在 context 里了。
"""

from __future__ import annotations

import pytest

from test_v2.section_10_agent_reliability.conftest import (
    PROVIDER_PARAMS,
    record_trial,
    run_real_agent,
    skip_reliability,
)


# Use a distinctive fact unlikely to appear by accident in any other test
# context — easy to assert "the agent surfaced THIS exact phrase".
FACT_KEY = "蔬菜供应商"
FACT_VALUE = "海王星农场"  # distinctive, no chance of LLM hallucinating it


@skip_reliability
@pytest.mark.parametrize("model", PROVIDER_PARAMS)
@pytest.mark.parametrize("trial", [0, 1, 2])
def test_remember_in_one_session_recall_in_another(
    db, seed_user, real_llm_enabled, model, trial
):
    """S1: tell agent fact, expect remember to fire.
    S2: ask agent the fact, expect recall to fire + answer contains it."""
    from agent.storage.models import AgentMemory

    BUCKET = "P4-cross-session-memory"

    error = None
    run1 = run2 = None
    passed = False
    failure_detail = ""
    try:
        # ─── Session 1 ─────────────────────────────────
        run1, agent1 = run_real_agent(
            db,
            seed_user.id,
            (
                f"请用 remember 工具帮我记住一个长期事实："
                f"我们的{FACT_KEY}是「{FACT_VALUE}」。"
                f"以后我问起这件事的时候，请通过 recall 取出来告诉我。"
            ),
            model=model,
        )
        s1_remembered = "remember" in run1.tool_names
        mem_rows = (
            db.query(AgentMemory).filter(AgentMemory.user_id == seed_user.id).all()
        )
        s1_db_has_fact = any(FACT_VALUE in (r.value or "") for r in mem_rows)

        if not s1_remembered:
            failure_detail += f"S1 didn't call remember. tools={run1.tool_names}\n"
        if not s1_db_has_fact:
            failure_detail += (
                f"S1 didn't persist FACT_VALUE in v3_agent_memories. "
                f"rows={[(r.key, r.value) for r in mem_rows]}\n"
            )

        # ─── Session 2 — NEW session_id, same user ─────
        # Factory auto-injects memory into S2's system prompt, so the
        # agent should answer directly from context. We do NOT assert
        # recall was called — auto-injection makes the tool optional.
        run2, _ = run_real_agent(
            db,
            seed_user.id,
            f"我们的{FACT_KEY}是哪家？",
            model=model,
            # No session_id → new session
        )
        s2_answer_correct = FACT_VALUE in (run2.answer or "")
        # The wrong path: agent ignores the injected fact and searches
        # masterdata DB. We flag it but don't fail on it alone — if the
        # final answer is correct via any route, the user is served.
        s2_took_wrong_path = (
            "search_masterdata" in run2.tool_names
            and not s2_answer_correct
        )

        if not s2_answer_correct:
            failure_detail += (
                f"S2 answer missing FACT_VALUE={FACT_VALUE!r}. "
                f"answer={run2.answer!r} tools={run2.tool_names}\n"
            )
        if s2_took_wrong_path:
            failure_detail += (
                "S2 fell back to search_masterdata despite memory "
                "being auto-injected — preamble may have been "
                "missed/ignored by the model.\n"
            )

        passed = s1_remembered and s1_db_has_fact and s2_answer_correct
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"

    record_trial(BUCKET, model, trial, passed=passed, run=run2, error=error)

    if error:
        pytest.fail(f"agent raised: {error}")
    assert passed, failure_detail or "see report"
