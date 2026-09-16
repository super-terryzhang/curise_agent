"""Token and cost budget with hard cap.

Distilled from hermes-agent/tools/budget_config.py + the usage tracking
scattered across run_agent.py.  We keep:
    - Per-turn token counters (prompt / completion / cached / reasoning)
    - Running total across the whole Agent.run() lifetime
    - Optional per-model USD pricing → running cost estimate
    - Hard cap: if either tokens or USD cross the cap, the Agent stops
      on the NEXT loop iteration with a structured reason

Why this matters: without this, a buggy agent looping under max_steps
can silently burn hundreds of dollars. Hermes learned this the hard way;
it has `budget_config.py` as the single source of truth for caps.

We only track what OpenAI / DeepSeek / Anthropic / most providers expose
in the `usage` field of the response. Unknown providers silently accrue 0.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any


log = logging.getLogger(__name__)


# Rough USD/1M-tokens pricing for a few common models. Users can pass a
# custom `price_per_1m` dict to cover others. These are deliberately
# approximate — for precise billing use the provider's own meters.
_DEFAULT_PRICING_PER_1M: dict[str, dict[str, float]] = {
    # DeepSeek (non-cache prices, as of early 2026).
    "deepseek-chat":      {"input": 0.14,  "output": 0.28},
    "deepseek-reasoner":  {"input": 0.55,  "output": 2.19},
    # OpenAI
    "gpt-4o-mini":        {"input": 0.15,  "output": 0.60},
    "gpt-4o":             {"input": 2.50,  "output": 10.00},
    "gpt-4.1":            {"input": 2.00,  "output": 8.00},
    "gpt-4.1-mini":       {"input": 0.40,  "output": 1.60},
    # Anthropic (approximate).
    "claude-sonnet-4-5":  {"input": 3.00,  "output": 15.00},
    "claude-opus-4-7":    {"input": 15.00, "output": 75.00},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
}


@dataclass
class Budget:
    """Running token + cost ledger with optional hard cap.

    Any field left as 0 / None means "no limit for this dimension".
    """

    model: str = ""
    # Hard caps — run stops when any is exceeded.
    max_input_tokens: int = 0
    max_output_tokens: int = 0
    max_total_tokens: int = 0
    max_usd: float = 0.0
    # Pricing override; if empty, we look the model up in _DEFAULT_PRICING_PER_1M.
    price_per_1m: dict[str, float] = field(default_factory=dict)

    # Running totals (updated via update_from_usage).
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0

    def update_from_usage(self, usage: dict[str, Any]) -> None:
        """Fold a provider `usage` dict into the running totals.

        Accepts the widest possible shape — OpenAI uses `prompt_tokens` /
        `completion_tokens`, Anthropic uses `input_tokens` / `output_tokens`,
        some providers expose cached/reasoning subtotals.
        """
        def _int(key: str) -> int:
            v = usage.get(key)
            try:
                return int(v or 0)
            except (TypeError, ValueError):
                return 0

        inp = _int("prompt_tokens") or _int("input_tokens")
        out = _int("completion_tokens") or _int("output_tokens")
        self.input_tokens += inp
        self.output_tokens += out

        # Nested details if the provider supplies them.
        details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
        if isinstance(details, dict):
            self.cached_input_tokens += int(details.get("cached_tokens") or 0)
        ctd = usage.get("completion_tokens_details") or usage.get("output_tokens_details")
        if isinstance(ctd, dict):
            self.reasoning_tokens += int(ctd.get("reasoning_tokens") or 0)

    # ---------- cost ----------

    def _pricing(self) -> dict[str, float]:
        if self.price_per_1m:
            return self.price_per_1m
        return _DEFAULT_PRICING_PER_1M.get(self.model, {})

    def estimated_usd(self) -> float:
        p = self._pricing()
        if not p:
            return 0.0
        cost_input = self.input_tokens * (p.get("input", 0.0) / 1_000_000)
        cost_output = self.output_tokens * (p.get("output", 0.0) / 1_000_000)
        return cost_input + cost_output

    # ---------- enforcement ----------

    def check(self) -> tuple[bool, str]:
        """Return (ok, reason_if_exhausted). Called before every LLM call."""
        if self.max_input_tokens and self.input_tokens >= self.max_input_tokens:
            return False, (
                f"input-token budget exhausted: "
                f"{self.input_tokens:,} >= {self.max_input_tokens:,}"
            )
        if self.max_output_tokens and self.output_tokens >= self.max_output_tokens:
            return False, (
                f"output-token budget exhausted: "
                f"{self.output_tokens:,} >= {self.max_output_tokens:,}"
            )
        total = self.input_tokens + self.output_tokens
        if self.max_total_tokens and total >= self.max_total_tokens:
            return False, (
                f"total-token budget exhausted: "
                f"{total:,} >= {self.max_total_tokens:,}"
            )
        if self.max_usd:
            spent = self.estimated_usd()
            if spent >= self.max_usd:
                return False, (
                    f"USD budget exhausted: ${spent:.4f} >= ${self.max_usd:.2f}"
                )
        return True, ""

    # ---------- reporting ----------

    def snapshot(self) -> dict[str, Any]:
        total = self.input_tokens + self.output_tokens
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": total,
            "estimated_usd": round(self.estimated_usd(), 6),
            "caps": {
                "max_input_tokens": self.max_input_tokens or None,
                "max_output_tokens": self.max_output_tokens or None,
                "max_total_tokens": self.max_total_tokens or None,
                "max_usd": self.max_usd or None,
            },
        }


class BudgetExceeded(Exception):
    """Raised when the Agent tries to make a call after the budget is exhausted."""

    def __init__(self, reason: str, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot
        super().__init__(reason)
