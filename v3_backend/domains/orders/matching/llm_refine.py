"""Fuzzy product matching — LLM-based refinement for items code_first missed.

Two backends, selected by config:
- `FakeMatcher`: pure-Python similarity (tokenized overlap). Zero deps, deterministic,
  the default in dev and tests.
- `GeminiMatcher`: optional plugin behind `GOOGLE_API_KEY`. Same interface.

The registry can be extended without touching `service.py` — just call
`register_matcher(name, factory)` from another module.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any, Protocol

from domains.masterdata import Product
from infrastructure.config import settings

logger = logging.getLogger(__name__)


class LLMMatcher(Protocol):
    name: str

    def refine(self, unmatched: list[dict[str, Any]], pool: list[Product]) -> dict[int, int]:
        """Given unmatched input products + candidate pool, return `{input_index: product_id}`.

        Only include entries where a match is confidently made — omit if unsure.
        """
        ...


# ─── Registry ─────────────────────────────────────────────────

_MATCHERS: dict[str, Callable[[], LLMMatcher]] = {}


def register_matcher(name: str, factory: Callable[[], LLMMatcher]) -> None:
    _MATCHERS[name] = factory


def get_matcher() -> LLMMatcher:
    """Pick the best available backend.

    - If GOOGLE_API_KEY is set → GeminiMatcher (when Phase 3+ plugs it in)
    - Otherwise → FakeMatcher (dev default, zero-cost)
    """
    preferred = "gemini" if settings.GOOGLE_API_KEY else "fake"
    factory = _MATCHERS.get(preferred) or _MATCHERS["fake"]
    return factory()


# ─── FakeMatcher ──────────────────────────────────────────────


_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


class FakeMatcher:
    """Token-overlap similarity. Good enough for Phase 3 dev + most test cases."""

    name = "fake"
    MIN_OVERLAP = 2
    MIN_JACCARD = 0.34

    def refine(self, unmatched: list[dict[str, Any]], pool: list[Product]) -> dict[int, int]:
        picks: dict[int, int] = {}
        for idx, inp in enumerate(unmatched):
            input_tokens = _tokens(inp.get("product_name") or "")
            if len(input_tokens) < 2:
                continue
            best_score = 0.0
            best_product_id: int | None = None
            for product in pool:
                candidate_tokens = _tokens(product.product_name_en or "") | _tokens(
                    product.product_name_jp or ""
                )
                if not candidate_tokens:
                    continue
                overlap = input_tokens & candidate_tokens
                if len(overlap) < self.MIN_OVERLAP:
                    continue
                jaccard = len(overlap) / max(1, len(input_tokens | candidate_tokens))
                if jaccard >= self.MIN_JACCARD and jaccard > best_score:
                    best_score = jaccard
                    best_product_id = product.id
            if best_product_id is not None:
                picks[idx] = best_product_id
        return picks


register_matcher("fake", FakeMatcher)


# ─── GeminiMatcher (Phase 3+, optional) ──────────────────────


class GeminiMatcher:
    """Gemini-backed matcher. Requires GOOGLE_API_KEY.

    Phase 3 keeps this as a stub — it returns the FakeMatcher's answer.
    The real Gemini integration lands when we stabilise on structured output.
    """

    name = "gemini"

    def __init__(self) -> None:
        self._fallback = FakeMatcher()

    def refine(self, unmatched: list[dict[str, Any]], pool: list[Product]) -> dict[int, int]:
        logger.info("GeminiMatcher: stub path — delegating to FakeMatcher")
        return self._fallback.refine(unmatched, pool)


register_matcher("gemini", GeminiMatcher)


# ─── Integration helper ──────────────────────────────────────


def apply_refinement(
    all_results: list[dict[str, Any]],
    unmatched: list[dict[str, Any]],
    pool: list[Product],
) -> None:
    """Mutate `all_results` in place: update not_matched entries that the matcher resolves."""
    if not unmatched:
        return
    matcher = get_matcher()
    picks = matcher.refine(unmatched, pool)
    if not picks:
        return

    product_by_id = {p.id: p for p in pool}

    # Map unmatched index → its position in all_results
    not_matched_positions = [
        i for i, r in enumerate(all_results) if r["match_status"] == "not_matched"
    ]
    for unmatched_idx, product_id in picks.items():
        if unmatched_idx >= len(not_matched_positions):
            continue
        result_idx = not_matched_positions[unmatched_idx]
        product = product_by_id.get(product_id)
        if product is None:
            continue
        all_results[result_idx]["match_status"] = "matched"
        all_results[result_idx]["match_score"] = 0.7
        all_results[result_idx]["match_reason"] = f"{matcher.name} 模糊匹配"
        from domains.orders.matching.code_first import serialize_db_product

        all_results[result_idx]["matched_product"] = serialize_db_product(product)
