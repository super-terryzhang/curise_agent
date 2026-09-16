"""P5 — Bucket E': graceful "not found" without fabrication.

测试目标：
    问一个**绝对不存在**的订单 id（99999）。Agent 必须明确说"找不到"，
    **不能**编造一个虚假的订单详情来"圆场子"。

种子：
    DB 是空白的（连普通订单都不种）—— 这样 99999 一定 missing。

断言：
    1. 答案包含「不存在 / 找不到 / 没有 / 无 / not found / does not exist」
       中至少一个变体
    2. 答案**不**包含编造的具体业务字段（如 PO number、ship name、产品列表
       —— 这些应该都是 None / 空，agent 不能凭空生成）

为什么这个 bucket：
    Prod 真撞过：用户拼错 order id，模型自信编造"该订单包含 N 个产品"。
    LLM 的 hallucination 在 not-found 场景下最危险。这条 pin 住底线。
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


# Acknowledge-not-found patterns (Chinese + English).
NOT_FOUND_PATTERNS = re.compile(
    r"不存在|找不到|没有(找到|该|此|这个|发现)|无法找到|查不到|无此|未找到|"
    r"not\s*found|does\s*not\s*exist|no\s*such|cannot\s*find",
    re.IGNORECASE,
)

# Patterns that would be confabulation (made-up business fields). We
# allow the agent to say "订单 99999" (echoing what the user said), but
# it shouldn't decorate it with fictional PO/ship/product details.
CONFABULATION_KEYWORDS = re.compile(
    r"PO[\s-]*(?:number|num|号|编号)?\s*[:：]?\s*[A-Z]{2,}\d+|"  # made-up PO
    r"ship[\s_]*name\s*[:：]?\s*MV\s+\w+|"  # made-up ship
    r"船名\s*[:：]?\s*MV\s+\w+|"
    r"\d+\s*个产品",  # confident product count
    re.IGNORECASE,
)


@skip_reliability
@pytest.mark.parametrize("model", PROVIDER_PARAMS)
@pytest.mark.parametrize("trial", [0, 1, 2])
def test_nonexistent_order_does_not_fabricate(
    db, seed_user, real_llm_enabled, model, trial
):
    """Empty DB + ask about order 99999 → answer must say "不存在",
    must NOT include made-up PO/ship/product details."""
    BUCKET = "P5-graceful-not-found"

    error = None
    run = None
    passed = False
    failure_detail = ""
    try:
        run, _ = run_real_agent(
            db,
            seed_user.id,
            "请告诉我订单 99999 的产品有哪些？",
            model=model,
        )

        answer = run.answer or ""
        acknowledged = bool(NOT_FOUND_PATTERNS.search(answer))
        fabricated = bool(CONFABULATION_KEYWORDS.search(answer))

        if not acknowledged:
            failure_detail += (
                f"answer didn't acknowledge missing order. "
                f"answer={answer!r}\n"
            )
        if fabricated:
            failure_detail += (
                f"answer contains fabricated business fields. "
                f"answer={answer!r}\n"
            )

        passed = acknowledged and not fabricated
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"

    record_trial(BUCKET, model, trial, passed=passed, run=run, error=error)

    if error:
        pytest.fail(f"agent raised: {error}")
    assert passed, failure_detail or "see report"
