"""Pin the meta-cognitive sections added to the system prompt
(2026-05-19, after the 5-turn weather session that failed).

These sections are LOAD-BEARING — they're the contract that says
"agent must answer YES when asked about HTML/visualization, must keep
calling tools until 24 hours of data is reached, must continue prior
topic on short follow-ups." Content-shape tests like these prevent
silent prompt drift (someone trims for token budget and we revert to
the failure mode).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def system_prompt() -> str:
    """Build the v3 chat agent's system prompt the same way prod does."""
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    os.environ.setdefault("SECRET_KEY", "t")
    os.environ.setdefault("ENV", "development")
    from agent.runtime.factory import _DEFAULT_SYSTEM_PROMPT
    return _DEFAULT_SYSTEM_PROMPT


# ─── Vocabulary mapping (P1/P2 fix) ─────────────────────────


def test_prompt_maps_html_vocabulary_to_artifact(system_prompt: str):
    """User phrasings → artifact channel.  Pre-fix, agent denied
    'HTML capability' because the system prompt had no 'HTML' token
    and no mapping from natural language to tools."""
    p = system_prompt
    # The word "HTML" must appear so the agent's attention catches user phrasings
    assert "HTML" in p, "Prompt must explicitly contain the token 'HTML'"
    # The mapping rule must say HTML → artifact
    assert "artifact" in p
    # Other common user phrasings should be enumerated
    for phrase in ("可视化", "图表", "富 UI"):
        assert phrase in p, f"User phrasing {phrase!r} must be mapped in prompt"


def test_prompt_forbids_denying_capability_without_introspecting(system_prompt: str):
    """Prevent the 'I don't have HTML capability' regression. The prompt
    must explicitly tell the agent to check its tool list before denying."""
    p = system_prompt
    # Loose check — must mention introspection / tool list / check
    assert "tool" in p.lower() and "check" in p.lower()
    # Must use the YES/NO answer guidance
    assert "answer YES" in p or "YES" in p or "Never deny" in p


# ─── Goal-completion (P3a fix) ───────────────────────────────


def test_prompt_has_goal_completion_rule(system_prompt: str):
    """Pre-fix: agent gave 6 hours when user asked for 24. The new
    section must compel the agent to verify quantity matches user spec
    and call more tools if short."""
    p = system_prompt
    # Must explicitly mention completing the user's quantitative request
    assert "Goal-completion" in p or "deliver N" in p or "quantitative" in p
    # Must mention the truncation marker recovery path
    assert "TRUNCATED" in p


# ─── Multi-turn continuity (P8 fix) ──────────────────────────


def test_prompt_has_topic_continuity_rule(system_prompt: str):
    """Pre-fix: turn 4 '按每小时列给我' was treated as new topic;
    agent asked 'what data?' instead of continuing the prior weather
    discussion."""
    p = system_prompt
    # Must say short follow-ups continue prior topic
    assert "continuity" in p.lower() or "CONTINUE" in p or "continues" in p
    # Should specifically cover the failure example
    assert "topic" in p.lower() or "subject" in p.lower()


# ─── P20 · Industry-verbatim persistence patterns ───────────


def test_prompt_has_persistence_directive(system_prompt: str):
    """OpenAI GPT-5 cookbook + Anthropic best practices both ship a
    persistence directive verbatim. Pin: at least one of the canonical
    phrasings ('keep going until completely resolved' / 'never
    artificially stop early') must be in our prompt. Without this the
    agent terminates after 2 failed retries (verified prod 2026-05-19,
    5-turn weather session)."""
    p = system_prompt
    # The directive's first-line opener
    assert "You are an agent" in p, (
        "OpenAI's canonical 'You are an agent' opener is missing"
    )
    # Persistence intent — one of the two canonical phrasings
    has_keep_going = "completely resolved" in p.lower() or "keep going" in p.lower()
    has_no_early_stop = "never artificially stop" in p.lower() or "do not stop tasks early" in p.lower()
    assert has_keep_going or has_no_early_stop, (
        "Persistence directive missing both canonical phrasings — "
        "agent will revert to early termination."
    )


def test_prompt_forbids_clarification_round_trips(system_prompt: str):
    """GPT-5 cookbook: 'Do not ask the human to confirm or clarify
    assumptions, as you can always adjust later — decide what the most
    reasonable assumption is, proceed with it.' Pin the verbatim
    intent."""
    p = system_prompt
    # Must forbid asking the user to clarify
    assert "Do NOT ask the user to clarify" in p or "do not ask the user to clarify" in p.lower(), (
        "Anti-clarification rule missing — agent will ask 'what data?' "
        "instead of proceeding with a reasonable assumption."
    )
    # Must tell agent to decide + document
    assert "assumption" in p.lower()
    assert "proceed" in p.lower() or "decide" in p.lower()


def test_prompt_says_retry_with_different_approach_on_tool_fail(system_prompt: str):
    """Gemini 3 prompt practices (P. Schmid): 'If a tool fails, analyze
    the error and try a different approach.' Pin this — without it,
    agent does identical retries (and identical re-failures) until it
    gives up. Verified prod 2026-05-19 — Kimi tried same wrong column
    shape 2× then quit."""
    p = system_prompt
    # The key phrase: tool failure → DIFFERENT approach
    assert "different approach" in p.lower() or "change something before retry" in p.lower(), (
        "Retry-with-different-approach rule missing — identical retries "
        "are the most common observed failure mode."
    )
    # Quantify: 2 identical retries == 1 failure
    assert "two" in p.lower() or "2" in p or "identical retries" in p.lower()


def test_prompt_has_pre_finish_verification_checklist(system_prompt: str):
    """Anthropic prompting best practices: 'Ask Claude to self-check.
    Append something like Before you finish, verify your answer against
    [test criteria]. This catches errors reliably.' Plus Devin: 'Tell
    Devin how to check its progress.' Pin: we must have a checklist
    that runs BEFORE the final finish call."""
    p = system_prompt
    # Section heading or equivalent
    assert "Before you finish" in p or "before you finish" in p.lower()
    # Must mention 'verify' and 'user'
    assert "verify" in p.lower()
    # Must include the most critical case from our prod incident:
    # claiming artifact was rendered when it wasn't.
    assert "NOT dispatched" in p or "no panel" in p.lower(), (
        "Pre-finish checklist must explicitly cover the 'claimed panel "
        "but artifact failed' failure mode (prod 2026-05-19)."
    )
