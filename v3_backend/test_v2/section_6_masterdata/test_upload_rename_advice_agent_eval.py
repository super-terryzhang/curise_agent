"""Section 6 — Bug B: agent must NOT suggest code-rename as a workaround.

This is the LLM behavior probe for the second half of the
strict-product-identity-contract change (2026-05-27).

Bug B (user-reported):
    When `preview_upload` showed "117 rows would UPDATE existing products"
    in a multi-port scenario, the agent suggested workarounds like adding
    suffixes to product codes (`99PRD010725-TK`) or appending port names
    to product names (`APPLE RED DELICIOUS (Tokyo)`). This is wrong
    because product codes are stable business identifiers tied to
    external systems (supplier catalogs, ERP) — forking the SKU silently
    breaks every downstream report that pivots on it.

What the fix did (deployed in cruise-v3-backend-00066-psj):
    SKILL.md picked up an explicit anti-pattern entry forbidding rename
    suggestions and prescribing the correct guidance ("add a `port`
    column to the Excel; same code at different ports = different
    records is the schema's design"). The strict matcher itself also
    makes country+port required, so the rename workaround would no
    longer even compile — but a misinformed agent could still suggest
    it before the user tries.

What this test pins:
    For a handful of natural user phrasings where pre-fix the agent
    would have offered a rename, assert the final reply:
      (a) does NOT contain any rename / suffix marker
      (b) mentions `port` (the correct workaround dimension) somewhere

Why we don't assert exact wording:
    LLM eval is fuzzy. We're not testing prose quality — we're testing
    that the *direction* of advice is right. A reply that says "请在
    Excel 加 port 列" passes. A reply that says "把代码改成
    99PRD010725-TK" fails. Adjust the forbidden token list if a real
    failure mode surfaces that we haven't anticipated yet.

How to run:
    PYTEST_RUN_SLOW=1 GOOGLE_API_KEY=... pytest \
        test_v2/section_6_masterdata/test_upload_rename_advice_agent_eval.py -v

This is `-m slow` by default — real Gemini calls cost money + flake.
"""

from __future__ import annotations

import os
import re

import pytest

_skip_real_gemini = pytest.mark.skipif(
    not os.getenv("PYTEST_RUN_SLOW")
    and not os.getenv("RUN_REAL_GEMINI_TESTS")
    or not os.getenv("GOOGLE_API_KEY"),
    reason="real-Gemini agent eval; needs GOOGLE_API_KEY + PYTEST_RUN_SLOW=1",
)


# Markers that, if they appear in the reply, mean the agent is suggesting
# the user mangle their product code/name. The list is intentionally
# narrow — we don't want to fail on the agent QUOTING the bad pattern in
# order to forbid it (e.g. "do NOT rename to `99PRD010725-TK`"), so we
# strip such quoted/negated mentions before matching.
_FORBIDDEN_RENAME_PATTERNS = [
    r"99PRD\d+-[A-Z]{2,3}",   # the literal user-screenshot example: 99PRD010725-TK
    r"-TK\b",                  # generic Tokyo suffix advice
    r"-TYO\b",
    r"_TYO\b",
    r"\(Tokyo\)",              # name-suffix advice
    r"\(東京\)",
    r"加.{0,8}后缀",           # zh-CN: "append suffix" advice
    r"添加.{0,8}后缀",
    r"修改.{0,8}产品代码",     # zh-CN: "modify product code"
    r"改.{0,8}产品代码",
    r"重命名.{0,8}产品",       # zh-CN: "rename product"
    r"rename.{0,8}(the )?(product )?code",   # en
    r"append.{0,8}suffix.{0,12}code",
]

_NEGATION_PREFIXES = (
    "不要",
    "千万不要",
    "禁止",
    "do not",
    "don't",
    "never",
    "avoid",
    "不应该",
)


def _strip_negated_mentions(text: str) -> str:
    """Remove sentences where the agent is correctly TELLING the user
    NOT to rename. We don't want to fail those.

    Approach: drop any sentence that contains a negation cue. Crude but
    sufficient — if the agent's advice is mixed (negates then offers as
    fallback), the surviving sentences still contain the forbidden
    pattern and the test still flags it.
    """
    sentences = re.split(r"(?<=[。！？.!?])\s+", text)
    kept = []
    for s in sentences:
        low = s.lower()
        if any(neg in low for neg in _NEGATION_PREFIXES):
            continue
        kept.append(s)
    return " ".join(kept)


# The prompts intentionally don't mention `port` — that's what we want
# the agent to bring up on its own, because Bug B was about the agent
# steering toward rename instead.
_RENAME_BAIT_PROMPTS = [
    # Direct replay of the user's reported situation
    "我有一个产品代码 99PRD010725，已经在 Sydney 港存在了。"
    "现在我要把它在 Tokyo 港也卖，需要怎么做？"
    "上传 Excel 系统说会 update 旧的，我想让 Sydney 那条保留不动。",
    # English flavor of the same situation
    "Product 99PRD010590 already exists in our system at Sydney with AUD price. "
    "Now I want to add it at Tokyo with a JPY price. The Excel upload preview "
    "says it will UPDATE the Sydney row. How do I keep Sydney untouched?",
    # User asks for the workaround directly — the bait
    "preview_upload 报告 117 个产品会被 update，我并不想改它们，"
    "这些产品在新港口应该是独立的记录。有什么办法绕过吗？",
]


@_skip_real_gemini
@pytest.mark.parametrize("user_message", _RENAME_BAIT_PROMPTS)
def test_agent_does_not_suggest_code_rename_for_multiport_upload(
    db, seed_user, user_message
):
    """Pre-fix Gemini would happily say "你可以把代码改成 99PRD010725-TK"
    when it saw the multi-port conflict. SKILL.md now forbids that
    pattern explicitly. Assert the deployed agent's advice is clean:
    no rename / suffix markers, and `port` is mentioned as the correct
    dimension to fix.
    """
    from agent.runtime.factory import create_v3_chat_agent

    agent = create_v3_chat_agent(
        db=db,
        user_id=seed_user.id,
        user_role="superadmin",
    )
    answer = agent.run(user_message)

    cleaned = _strip_negated_mentions(answer)

    offending = []
    for pat in _FORBIDDEN_RENAME_PATTERNS:
        m = re.search(pat, cleaned, flags=re.IGNORECASE)
        if m:
            offending.append((pat, m.group(0)))

    assert not offending, (
        f"[{user_message[:40]}…] agent SUGGESTED a code-rename "
        f"workaround. Bug B is back. offending: {offending}\n"
        f"---full answer---\n{answer}"
    )

    # Positive assertion: the agent should mention `port` (the right
    # dimension to add to Excel). Use case-insensitive substring on
    # `port` / `港口` / `港` — at least one must appear, otherwise the
    # agent gave generic non-actionable advice.
    answer_low = answer.lower()
    mentions_port = (
        "port" in answer_low
        or "港口" in answer
        or "港" in answer
    )
    assert mentions_port, (
        f"[{user_message[:40]}…] agent did NOT mention port as the "
        f"correct dimension. Advice is generic. answer: {answer!r}"
    )
