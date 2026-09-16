"""Section 6 — Masterdata upload, LLM behavior eval.

测试目标：
    真调 Gemini 跑 master-data-upload skill 的 Step 0 路径，断言：
      1. agent 触发 `load_skill("master-data-upload")`
      2. agent 调用 `get_upload_template()` 工具（**不能只口头让用户去下载**）
      3. 最终回复里**真的包含**下载 URL（`/api/data-upload/template`）

    这是 LLM 行为层测试 —— 比 dispatch 单元测试更进一步：dispatch 只验
    "如果调用了工具，会返回正确内容"；这个测试验"agent 真的会自己决定
    调用工具"。

为什么必须有：
    2026-05-14 真实事故：v13 部署后用户说"我想上传一份 Excel 来批量更新
    产品数据"，agent 触发了 skill，但只**口头**说"请先下载产品上传模板"
    而没调 `get_upload_template`，用户看不到链接。所有 unit / endpoint /
    self-consistency 测试都绿，但用户视角是"功能坏的"。
    根因是 SKILL.md 措辞 + 缺 no-batch-id worked example，让 LLM 解读
    Step 0 为"告诉用户去下载"而非"调工具拿链接"。

设计方法：
    - 真调 Gemini（需要 `GOOGLE_API_KEY`）
    - 默认 skip（CI 跑得起一两次但不适合每次 push 都跑，参考
      `test_large_scanned_pdf_ocr.py` 的 `PYTEST_RUN_SLOW` 约定）
    - 单测跑 1 次；偶尔 flaky 是可接受的（Gemini 非确定）。本地手动跑
      时如果连续失败 3 次，回头看 SKILL.md 的 Step 0 措辞而非这个测试。
    - 多种用户措辞并行参数化，覆盖各种 trigger 同义说法。

如何运行：
    pytest test_v2/section_6_masterdata/test_upload_template_agent_eval.py -m slow
    PYTEST_RUN_SLOW=1 pytest test_v2/section_6_masterdata/test_upload_template_agent_eval.py -v
"""

from __future__ import annotations

import os

import pytest

# Skip unless explicitly opted in — same pattern as
# `test_large_scanned_pdf_ocr.py`. Real LLM calls cost money + are slow.
_skip_real_gemini = pytest.mark.skipif(
    not os.getenv("PYTEST_RUN_SLOW")
    and not os.getenv("RUN_REAL_GEMINI_TESTS")
    or not os.getenv("GOOGLE_API_KEY"),
    reason="real-Gemini agent eval; needs GOOGLE_API_KEY + PYTEST_RUN_SLOW=1",
)


# Different ways a real user might ask for the upload — each must trigger
# the skill AND the template tool. Phrasings must contain an explicit
# upload/Excel/template signal — pure "batch update prices" (no medium
# named) is ambiguous (the user might want SQL or a dashboard) and the
# agent's correct response there is to clarify, not auto-emit a template.
_UPLOAD_INTENT_PHRASINGS = [
    "我想上传一份 Excel 来批量更新产品数据",  # the original prod regression
    "我有一份产品 excel 想上传",
    "I want to bulk import some products",
    "怎么用模板更新产品",
    "用 Excel 批量更新产品价格",  # explicit medium + intent
]


@_skip_real_gemini
@pytest.mark.parametrize("user_message", _UPLOAD_INTENT_PHRASINGS)
def test_agent_calls_get_upload_template_when_user_has_no_batch_id(
    db, seed_user, user_message
):
    """When the user signals "I want to upload products" without a
    batch_id or file attachment, the agent MUST call get_upload_template
    and the final reply MUST contain the download URL. Pre-fix (v13) the
    agent loaded the skill but skipped the tool, leaving the user with
    "please download the template" and no link.
    """
    from agent.runtime.factory import create_v3_chat_agent

    user = seed_user
    agent = create_v3_chat_agent(
        db=db,
        user_id=user.id,
        user_role="superadmin",  # any writer role is fine; superadmin to skip ACL noise
    )
    answer = agent.run(user_message)

    tool_names = [s.name for s in agent.trace if s.kind == "tool"]

    # 1. The user-visible promise is "template tool was called". If the
    # agent jumps straight there without `load_skill` (it has the tool
    # in its catalog and may decide to skip the playbook), that's fine —
    # what matters is the link reaching the user. So we don't assert
    # `load_skill in tool_names`; we only assert the tool that produces
    # the link.
    assert "get_upload_template" in tool_names, (
        f"[{user_message!r}] agent did NOT call get_upload_template. "
        f"This is the prod regression — agent will only TELL the user "
        f"to download, not give them the link. "
        f"trace tools: {tool_names!r}. final answer: {answer!r}"
    )

    # 2. Download URL must reach the user's screen in the final reply.
    # Guard against hallucinated URLs (`example.com/template.xlsx`,
    # `placeholder` etc.) — the only correct URL points at our endpoint.
    assert "/api/data-upload/template" in answer, (
        f"[{user_message!r}] template URL missing from final reply "
        f"(or agent invented a fake URL). answer: {answer!r}"
    )
    # Block known hallucination markers — the LLM occasionally invents
    # an `example.com` URL when it skips the tool call.
    assert "example.com" not in answer.lower(), (
        f"[{user_message!r}] agent fabricated a placeholder URL: {answer!r}"
    )


@_skip_real_gemini
def test_agent_skips_template_when_user_already_has_batch_id(db, seed_user):
    """The reverse: when the user already references a batch_id, Step 0
    must be skipped (otherwise we'd spam them with a template they don't
    need). Agent should jump straight into parse_uploaded_file.
    """
    from agent.runtime.factory import create_v3_chat_agent

    # Seed a batch the agent can actually look at — otherwise parse_uploaded_file
    # would return "Error: batch X not found" and we couldn't tell what the
    # agent's intent was vs. its tool's failure.
    from domains.masterdata.upload import parse_excel
    from test_v2.fixtures.helpers import make_excel

    blob = make_excel([{"product_name": "Seed Product"}])
    batch = parse_excel(db, file_bytes=blob, filename="seed.xlsx", user_id=seed_user.id)

    agent = create_v3_chat_agent(
        db=db, user_id=seed_user.id, user_role="superadmin"
    )
    agent.run(f"我刚上传了 batch_id={batch.id}，看一下质量")

    tool_names = [s.name for s in agent.trace if s.kind == "tool"]

    # Whether the agent went through `load_skill` first or jumped
    # straight to `parse_uploaded_file` doesn't matter — both shapes
    # serve the user. What matters: it did NOT send the template.
    assert "get_upload_template" not in tool_names, (
        f"agent sent template even though user supplied batch_id. "
        f"trace tools: {tool_names}"
    )

    # And it did proceed to inspect the batch (the user's actual ask).
    assert "parse_uploaded_file" in tool_names, (
        f"agent didn't proceed to parse the batch. trace tools: {tool_names}"
    )
