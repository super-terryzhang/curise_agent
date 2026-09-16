"""Section 9 — Web API: lock in the approval-followup reflection contract.

测试目标：
    "用户上传后, skill 必须验证 + 反思 + 通知" 是产品要求 (2026-05-18)。
    实现分两层：
      (1) apps/http/chat.py 的 synthetic approval-followup prompt
          —— **不再**用 "1-2 句话 / 不要再调用任何工具" 这种硬约束
      (2) skills/master-data-upload/SKILL.md 的 **Step 5b** 反思模板
          —— 提供 ✅/⚠️/❌ 三档分类 + 错误分组 + 具体建议

    这条测试是 content-shape 守卫，不调 LLM、不调网络，
    确保两份文件的关键 contract 不被无意改坏。

    如果有人未来把 "1-2 句话" 加回来，或者删了 Step 5b，
    这个测试会立即报警。

为什么不调真 LLM：
    LLM 行为测试昂贵（每跑一次 ~30s + 算钱 + 撞 Moonshot quota）。
    在 contract 边界做 content-shape check 是经济的回归保护；
    真 LLM 跑分留给 section 10 reliability 套件做。
"""

from __future__ import annotations

import re
from pathlib import Path

# Resolve relative to test_v2/section_9_web_api/ → v3_backend/
_V3_BACKEND = Path(__file__).resolve().parent.parent.parent

CHAT_PY = _V3_BACKEND / "apps" / "http" / "chat.py"
UPLOAD_SKILL = _V3_BACKEND / "skills" / "master-data-upload" / "SKILL.md"


# ─── chat.py synthetic prompt contract ──────────────────────


def test_chat_synthetic_prompt_drops_hard_one_sentence_rule():
    """旧版有 '请用 1-2 句中文告诉用户' —— 限制太死，复杂 result（多错误）
    无法用一句话讲清。新版应放宽为"按内容详略"。"""
    body = CHAT_PY.read_text(encoding="utf-8")
    assert "请用 1-2 句中文告诉用户" not in body, (
        "synthetic prompt 不应再硬规定 '1-2 句' —— 详略由 result 内容决定"
    )


def test_chat_synthetic_prompt_drops_hard_no_tools_rule():
    """旧版 '不要再调用任何工具' 阻止 agent 在 followup turn 调
    preview_upload 查诊断细节。新版允许 read-only 工具。"""
    body = CHAT_PY.read_text(encoding="utf-8")
    assert "不要再调用任何工具" not in body, (
        "synthetic prompt 不应硬禁止工具调用 —— "
        "需允许调 preview_upload 等只读工具获取更深细节"
    )


def test_chat_synthetic_prompt_still_blocks_chained_propose_action():
    """放宽工具调用但**不可**放宽 propose_action ——
    一旦允许 followup 再发 propose，HITL 会无限套娃。"""
    body = CHAT_PY.read_text(encoding="utf-8")
    # Must contain a phrase explicitly forbidding new propose_action.
    forbidding_propose = (
        "不要发起新的 propose_action" in body
        or "不要重新发起 propose_action" in body
        or "Do not call propose_action" in body
    )
    assert forbidding_propose, (
        "synthetic prompt 必须**明确**禁止 followup turn 再触发 propose_action"
    )


def test_chat_synthetic_prompt_mentions_skill_delegation():
    """新版应**指引** agent 在结果复杂时按对应 skill 的规范输出
    （而不是把所有逻辑塞进 synthetic prompt 本身）。"""
    body = CHAT_PY.read_text(encoding="utf-8")
    # Look for explicit hand-off to skills (any phrasing that mentions skill).
    assert "skill" in body.lower(), (
        "synthetic prompt 应提示 agent 复杂场景按对应 skill 输出"
    )


# ─── SKILL.md Step 5b contract ──────────────────────────────


def test_skill_has_step_5b_section_header():
    body = UPLOAD_SKILL.read_text(encoding="utf-8")
    assert "Step 5b" in body, "SKILL.md 必须包含 Step 5b (反思+通知模板)"


def test_skill_step_5b_covers_three_classification_states():
    """三档分类是 contract 核心 —— 任何一档丢失都会让 agent 漏掉一种 case。"""
    body = UPLOAD_SKILL.read_text(encoding="utf-8")
    # The three status markers must all appear in the SKILL body
    # (in the Step 5b template section).
    assert "✅" in body, "Step 5b 缺全成功标记 ✅"
    assert "⚠️" in body, "Step 5b 缺部分成功标记 ⚠️"
    assert "❌" in body, "Step 5b 缺全失败标记 ❌"


def test_skill_step_5b_references_error_details():
    """Step 5b 反思的输入是 `error_details` 字段（v30 后 dispatch 返回的）。
    skill 必须显式提到这个字段，否则 agent 不知道往哪看。"""
    body = UPLOAD_SKILL.read_text(encoding="utf-8")
    assert "error_details" in body, (
        "Step 5b 必须提到 error_details 字段 —— 这是反思的数据来源"
    )


def test_skill_step_5b_has_concrete_next_step_library():
    """没有具体建议的反思 = 反思一半。skill 必须为常见错误类型给出具体 fix。"""
    body = UPLOAD_SKILL.read_text(encoding="utf-8")
    # At least the 3 most common errors we've seen in prod must be covered.
    assert re.search(r"supplier.*not found|FK not found|主数据", body, re.S), (
        "Step 5b 应覆盖 FK not found 类错误的修复建议"
    )
    assert "IntegrityError" in body or "uix_country_product_name_port" in body, (
        "Step 5b 应覆盖唯一约束冲突类错误的修复建议"
    )
    assert "preview_upload" in body, (
        "Step 5b 应在 other 兜底建议里引用 preview_upload 让用户查细节"
    )


def test_skill_step_5b_explicitly_forbids_propose_chain():
    """对应 chat.py 的硬规则 —— skill 内部也明确不能 chain propose_action。
    双重防御：chat.py prompt 是软提示，skill 是硬规则。"""
    body = UPLOAD_SKILL.read_text(encoding="utf-8")
    # Allow any phrasing that conveys "don't start a new propose_action".
    assert re.search(
        r"DO NOT call\s*`?propose_action`?|do not call propose_action|"
        r"不要.*propose_action|不要发起新的 propose|不要再发起",
        body, re.IGNORECASE,
    ), "Step 5b 必须明确禁止再调 propose_action（防止 HITL 套娃）"
