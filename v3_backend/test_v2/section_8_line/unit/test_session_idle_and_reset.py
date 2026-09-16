"""Section 8 — apps/line/session.py: SessionSource + idle timeout + reset keywords.

测试目标：
    会话辅助层定义了三件事：(1) SessionSource 描述消息来自哪里
    （DM/群、谁是 sender、用哪个 key 查权限），(2) 多久算 idle 应该开新会话，
    (3) 哪些关键字应被识别为"重置对话"。

为什么重要：
    - SessionSource.platform_user_key 决定 RBAC 查谁的权限 —— 群里必须用
      个人 sender 而非 group_id；记错了就是越权 bug。
    - idle 阈值控制跨天对话上下文是否累积过期；早一秒晚一秒都是 UX 决策。
    - reset keyword 是用户重新开始会话的逃生口；模糊匹配（"新对话啊"）
      不能误触发，否则真实查询被吃掉。

设计方法：
    SessionSource 是 frozen dataclass — 直接构造 + 断言 property。
    is_session_idle 用 monkey-injected `now` 避免 wall-clock 抖动。
    is_reset_keyword 用 parametrize 把多语言 / 大小写 / 边界情况一次过。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta

import pytest

from apps.line.session import (
    RESET_KEYWORDS,
    SESSION_IDLE_TIMEOUT_MINUTES,
    SessionSource,
    is_reset_keyword,
    is_session_idle,
)


# ─── is_session_idle ──────────────────────────────────────────


def test_idle_when_last_activity_is_none() -> None:
    """从未活跃 → idle —— 调用方据此决定要不要开新会话。"""
    assert is_session_idle(None) is True


def test_idle_when_activity_is_well_past_threshold() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0)
    stale = now - timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES + 10)
    assert is_session_idle(stale, now=now) is True


def test_not_idle_when_activity_is_recent() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0)
    recent = now - timedelta(minutes=5)
    assert is_session_idle(recent, now=now) is False


def test_not_idle_exactly_at_threshold_boundary() -> None:
    """生产代码用 `last_activity < cutoff` —— 恰好等于阈值不算 idle，
    刚到点的会话还能再用一次。"""
    now = datetime(2026, 1, 1, 12, 0, 0)
    exactly = now - timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)
    assert is_session_idle(exactly, now=now) is False


def test_idle_one_second_past_threshold() -> None:
    """阈值 + 1s 立即视为 idle。"""
    now = datetime(2026, 1, 1, 12, 0, 0)
    just_past = now - timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES, seconds=1)
    assert is_session_idle(just_past, now=now) is True


# ─── is_reset_keyword ─────────────────────────────────────────


@pytest.mark.parametrize(
    "kw",
    ["新对话", "重置", "新建对话", "reset"],
)
def test_reset_keyword_exact_match_triggers(kw: str) -> None:
    assert is_reset_keyword(kw) is True


@pytest.mark.parametrize("kw", ["RESET", "Reset", "rEsEt"])
def test_reset_keyword_english_is_case_insensitive(kw: str) -> None:
    """英文关键字大小写不敏感 —— 用户不必准确打字。"""
    assert is_reset_keyword(kw) is True


@pytest.mark.parametrize("kw", ["  新对话  ", "\treset\n", "  重置"])
def test_reset_keyword_trims_surrounding_whitespace(kw: str) -> None:
    """前后空白会被剥离 —— 移动键盘常带前导空格。"""
    assert is_reset_keyword(kw) is True


@pytest.mark.parametrize(
    "text",
    [
        "新对话啊",          # 尾巴多了字 — 不应匹配
        "再来一次新对话",     # 包含但非整体
        "please reset this", # 英文也只接受整体
        "hello",
        "",
        "   ",
    ],
)
def test_reset_keyword_rejects_partial_or_substring(text: str) -> None:
    """精确匹配 — 不模糊。否则用户问"重置订单"会被吃掉。"""
    assert is_reset_keyword(text) is False


def test_reset_keywords_set_is_frozen() -> None:
    """暴露给生产代码的是 frozenset —— 防止运行时被意外突变。"""
    assert isinstance(RESET_KEYWORDS, frozenset)


# ─── SessionSource ────────────────────────────────────────────


def test_session_source_dm_properties() -> None:
    s = SessionSource(
        platform="line",
        channel_id="default",
        chat_id="Uabc",
        chat_type="dm",
        user_id="Uabc",
    )
    assert s.is_group is False
    # DM 下 chat_id == user_id，permission key 就是 sender 自己
    assert s.platform_user_key == "Uabc"


def test_session_source_group_uses_sender_for_permission() -> None:
    """关键 RBAC 决策：群里查权限要用 *sender* 而非 group_id —— 否则
    所有群成员共用同一权限。"""
    s = SessionSource(
        platform="line",
        channel_id="default",
        chat_id="Cgroup-xyz",
        chat_type="group",
        user_id="Usender-abc",
    )
    assert s.is_group is True
    assert s.platform_user_key == "Usender-abc"
    # 不能用 chat_id 当 permission key
    assert s.platform_user_key != s.chat_id


def test_session_source_is_frozen_dataclass() -> None:
    """frozen=True —— 一旦构造好不可变，避免下游意外改 chat_id。"""
    s = SessionSource(
        platform="line",
        channel_id="default",
        chat_id="Uabc",
        chat_type="dm",
        user_id="Uabc",
    )
    with pytest.raises(FrozenInstanceError):
        s.chat_id = "tampered"  # type: ignore[misc]
