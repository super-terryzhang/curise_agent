"""Pluggable context engines — how a long conversation fits under a token cap.

Abstracts over hermes-agent's `agent/context_engine.py` (184 LOC) + its two
implementations (`context_compressor.py`, 766 LOC default; plus LCM plugin).

Shapes:

    ContextEngine (ABC)
        ├── TrimEngine        — current general-agent behavior (drop middle turns)
        └── CompressingEngine — hermes-style (LLM summarizes middle into a handoff)

Each engine is stateless w.r.t. the `Context` object but carries internal
state (`compression_count`, `_previous_summary`) between calls.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable


log = logging.getLogger(__name__)


_CHARS_PER_TOKEN = 4  # rough heuristic; matches hermes


def approx_tokens(msg: dict[str, Any]) -> int:
    """Coarse message-token estimate (shared by engines)."""
    chars = 0
    content = msg.get("content")
    if isinstance(content, str):
        chars += len(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                chars += len(part.get("text", "")) + 20
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        chars += len(str(fn.get("arguments", ""))) + len(str(fn.get("name", "")))
    return max(1, chars // _CHARS_PER_TOKEN)


def total_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(approx_tokens(m) for m in messages)


def sanitize_tool_pairs(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop any orphaned tool_call (no matching tool result) or orphaned tool
    result (no matching preceding tool_call).  Mirrors hermes _sanitize_tool_pairs.
    """
    # Collect tool_call_ids that appear in assistant tool_calls.
    call_ids: set[str] = set()
    for m in messages:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                cid = tc.get("id") if isinstance(tc, dict) else None
                if cid:
                    call_ids.add(cid)
    result_ids: set[str] = {
        m.get("tool_call_id") for m in messages if m.get("role") == "tool" and m.get("tool_call_id")
    }

    valid_pairs = call_ids & result_ids

    out: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            kept = [
                tc for tc in m["tool_calls"]
                if (tc.get("id") if isinstance(tc, dict) else None) in valid_pairs
            ]
            if kept:
                mm = dict(m)
                mm["tool_calls"] = kept
                out.append(mm)
            else:
                # Drop this assistant message entirely; it has no surviving tool call.
                if m.get("content"):
                    mm = dict(m)
                    mm.pop("tool_calls", None)
                    out.append(mm)
        elif m.get("role") == "tool":
            if m.get("tool_call_id") in valid_pairs:
                out.append(m)
        else:
            out.append(m)
    return out


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class ContextEngine(ABC):
    """Interface for deciding-when and deciding-how to compact a message list.

    Engines are long-lived for the Agent's life and may carry state across
    compactions (e.g. previous summary).
    """

    # Displayed in logs and manifests.
    name: str = "base"

    # How many head messages to preserve untouched.
    protect_first_n: int = 3

    # Compression trigger (token count).  0 = never; set by Agent from config.
    threshold_tokens: int = 0

    # Informational — number of times compact() has fired this session.
    compression_count: int = 0

    @abstractmethod
    def should_compress(self, messages: list[dict[str, Any]]) -> bool:
        """Return True if the engine wants to act on this message list."""

    @abstractmethod
    def compress(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return a (possibly shorter) message list that fits under budget.

        Must preserve tool_call/result pairing integrity.
        """


# ---------------------------------------------------------------------------
# TrimEngine — the existing general-agent behavior, now named
# ---------------------------------------------------------------------------

class TrimEngine(ContextEngine):
    """Lossy-by-drop: keep first user + newest tail; insert a trim marker.

    No LLM calls. Fast, zero-cost, but loses everything in the middle.
    Default engine for cheap / offline use.
    """

    name = "trim"

    def __init__(self, threshold_tokens: int = 15_000, protect_first_n: int = 1) -> None:
        self.threshold_tokens = threshold_tokens
        self.protect_first_n = protect_first_n

    def should_compress(self, messages: list[dict[str, Any]]) -> bool:
        return total_tokens(messages) > self.threshold_tokens

    def compress(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(messages) <= 6:
            return messages

        first_user_idx = next(
            (i for i, m in enumerate(messages) if m.get("role") == "user"), 0
        )
        head = messages[: first_user_idx + 1]
        tail = list(messages[first_user_idx + 1 :])

        budget = self.threshold_tokens
        while tail and (
            sum(approx_tokens(m) for m in head) + sum(approx_tokens(m) for m in tail) > budget
        ):
            dropped = tail.pop(0)
            if dropped.get("role") == "assistant" and dropped.get("tool_calls"):
                pending_ids = {tc["id"] for tc in dropped["tool_calls"]}
                tail = [
                    m for m in tail
                    if not (m.get("role") == "tool" and m.get("tool_call_id") in pending_ids)
                ]

        marker = {
            "role": "user",
            "content": "[context trimmed: earlier turns were dropped to stay under budget]",
        }
        self.compression_count += 1
        return sanitize_tool_pairs(head + [marker] + tail)


# ---------------------------------------------------------------------------
# CompressingEngine — hermes-style structured LLM summary
# ---------------------------------------------------------------------------

_SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION] Earlier turns in this conversation were compacted "
    "into the structured summary below. The work described is already done — "
    "files may already be changed, facts already gathered. Build on this, "
    "don't redo it."
)

_INITIAL_SUMMARY_PROMPT = """You are compacting an agent conversation to fit the context window.

Summarize the turns I'll paste below using EXACTLY this structure:

## Goal
(one sentence — what the user asked for)

## Progress
### Done
- (bullet) (bullet) (bullet)
### In Progress
- (bullet, if any — leave empty if none)

## Key Decisions
- (bullet: decisions + brief reason)

## Files / Artifacts
- `path` — what it contains / why it matters

## Next Steps
- (bullet) (bullet)

Be concrete: keep specific names, numbers, URLs, error messages. Drop small
talk and duplicate tool output. If a fact is only useful once and was acted
on, you can drop it. Target length: about {target} words."""


_UPDATE_SUMMARY_PROMPT = """You are updating a running handoff summary of an agent conversation.

Below you'll see the PREVIOUS SUMMARY followed by NEW turns. Produce a NEW
summary in the SAME structure (Goal / Progress / Key Decisions / Files /
Next Steps).

Rules:
- PRESERVE existing info in the previous summary that still matters.
- ADD new progress from the new turns.
- MOVE items from "In Progress" to "Done" when complete.
- REMOVE info only if clearly obsolete (e.g. a decision was reversed).
- Keep concrete names, paths, URLs, numbers.
- Target length: about {target} words."""


class CompressingEngine(ContextEngine):
    """Hermes-style compression: prune tool output → LLM summary of middle turns.

    Requires a `summarize(text) -> str` callable — pass in `agent.llm.LLM`
    or any OpenAI-compatible client wrapper.  Falls back to drop-trim if the
    summarizer raises.
    """

    name = "compressor"

    def __init__(
        self,
        summarizer: Callable[[str, int], str],
        *,
        threshold_tokens: int = 20_000,
        protect_first_n: int = 3,
        tail_token_budget: int = 6_000,
        summary_target_words: int = 400,
        min_messages: int = 8,
    ) -> None:
        """
        Args:
            summarizer: callable(prompt, max_tokens) -> str; raise on failure.
            threshold_tokens: fire compact() when total exceeds this.
            protect_first_n: first N messages are never compacted (system + first exchange).
            tail_token_budget: recent-tail tokens that stay verbatim.
            summary_target_words: target length of the generated summary.
            min_messages: skip compression if fewer than this many messages.
        """
        self.summarize = summarizer
        self.threshold_tokens = threshold_tokens
        self.protect_first_n = protect_first_n
        self.tail_token_budget = tail_token_budget
        self.summary_target_words = summary_target_words
        self.min_messages = min_messages

        self._previous_summary: str | None = None
        self._failed = False  # latch: if summary fails, fall back to trim for the rest of the session

    def should_compress(self, messages: list[dict[str, Any]]) -> bool:
        if self._failed:
            return False
        return total_tokens(messages) > self.threshold_tokens

    def compress(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        n = len(messages)
        if n < self.min_messages:
            return messages

        # Phase 1: cheap pre-pass — replace old tool-result bodies with a placeholder.
        messages = self._prune_old_tool_results(messages)

        # Phase 2: boundaries.  head = first N, tail = most recent messages whose
        # tokens fit inside tail_token_budget.
        head_end = min(self.protect_first_n, n - 2)
        tail_start = self._find_tail_start(messages, head_end)
        if head_end >= tail_start:
            return messages
        middle = messages[head_end:tail_start]
        if not middle:
            return messages

        # Phase 3: LLM summary.
        try:
            summary_body = self._summarize_middle(middle)
        except Exception as e:
            log.warning("summary failed, falling back to trim: %s", e)
            self._failed = True
            # Fall back to a static marker so the model knows something was dropped.
            summary_body = (
                "(Summary generation was unavailable. "
                f"{len(middle)} earlier turns were removed to save context.)"
            )

        summary_msg = {
            "role": "user",
            "content": f"{_SUMMARY_PREFIX}\n\n{summary_body}",
        }
        out = messages[:head_end] + [summary_msg] + messages[tail_start:]
        out = sanitize_tool_pairs(out)

        self._previous_summary = summary_body
        self.compression_count += 1
        return out

    # ---------- internals ----------

    def _prune_old_tool_results(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Replace old tool results' bodies with a compact placeholder.

        Keeps the tool_call/result pairing intact but reclaims bytes. Tool
        results inside the tail budget window are left untouched.
        """
        if len(messages) < self.min_messages:
            return messages
        # Determine which indices are "in the tail" — we leave those alone.
        tail_start = self._find_tail_start(messages, self.protect_first_n)
        out: list[dict[str, Any]] = []
        pruned = 0
        for i, m in enumerate(messages):
            if i < tail_start and m.get("role") == "tool":
                content = m.get("content") or ""
                if len(content) > 300:
                    mm = dict(m)
                    mm["content"] = "[Old tool output cleared to save context space]"
                    out.append(mm)
                    pruned += 1
                    continue
            out.append(m)
        if pruned:
            log.debug("pre-compress: pruned %d old tool results", pruned)
        return out

    def _find_tail_start(self, messages: list[dict[str, Any]], min_idx: int) -> int:
        """Return the lowest index i such that tokens(messages[i:]) <= tail_token_budget."""
        n = len(messages)
        running = 0
        # Walk backwards accumulating tokens until we exceed the budget.
        for i in range(n - 1, min_idx - 1, -1):
            running += approx_tokens(messages[i])
            if running > self.tail_token_budget:
                return i + 1
        return max(min_idx, 0)

    def _format_message_for_summary(self, m: dict[str, Any]) -> str:
        role = m.get("role", "?")
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                calls.append(f"{fn.get('name','?')}({str(fn.get('arguments',''))[:200]})")
            content = m.get("content") or ""
            return f"[assistant] {content}\n  tool_calls: {'; '.join(calls)}"
        if role == "tool":
            return f"[tool:{m.get('name','?')}] {str(m.get('content',''))[:600]}"
        if role == "user":
            return f"[user] {str(m.get('content',''))[:600]}"
        if role == "assistant":
            return f"[assistant] {str(m.get('content',''))[:600]}"
        return f"[{role}] {str(m.get('content',''))[:400]}"

    def _summarize_middle(self, middle: list[dict[str, Any]]) -> str:
        transcript = "\n\n".join(self._format_message_for_summary(m) for m in middle)
        target = self.summary_target_words

        if self._previous_summary:
            prompt = _UPDATE_SUMMARY_PROMPT.format(target=target)
            user_content = (
                f"PREVIOUS SUMMARY:\n{self._previous_summary}\n\n"
                f"---\nNEW TURNS TO MERGE IN:\n\n{transcript}"
            )
        else:
            prompt = _INITIAL_SUMMARY_PROMPT.format(target=target)
            user_content = f"TURNS TO SUMMARIZE:\n\n{transcript}"

        # Scale max_tokens: target words ≈ target × 1.5 tokens, plus slack.
        max_tokens = max(600, min(4000, int(target * 2.5)))
        return self.summarize(prompt + "\n\n" + user_content, max_tokens).strip()
