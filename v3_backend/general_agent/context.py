"""Message history + engine-driven compaction.

Thin wrapper around a message list. The `ContextEngine` plugin decides when
and how to compact.  Default engine is `TrimEngine` (preserves existing
general-agent behavior — no LLM calls).

Swap in `CompressingEngine` from `context_engine.py` to get hermes-style
structured-summary handoffs.
"""

from __future__ import annotations

from typing import Any

from .context_engine import ContextEngine, TrimEngine


class Context:
    def __init__(
        self,
        system_prompt: str,
        *,
        engine: ContextEngine | None = None,
        token_budget: int = 60_000,
    ) -> None:
        self.system_prompt = system_prompt
        self.messages: list[dict[str, Any]] = []
        # Default: TrimEngine; `token_budget` is the threshold in tokens
        # (matches the old coarse 4-chars/token heuristic used by the code
        # that predates the engine abstraction).
        self.engine: ContextEngine = engine or TrimEngine(threshold_tokens=token_budget)

    # ---------- append helpers ----------

    def add_user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, message: Any) -> None:
        if hasattr(message, "model_dump"):
            d = message.model_dump(exclude_none=True)
        elif isinstance(message, dict):
            d = dict(message)
        else:
            d = {"role": "assistant", "content": str(message)}
        d.setdefault("role", "assistant")
        if d.get("content") is None and not d.get("tool_calls"):
            d["content"] = ""
        self.messages.append(d)

    def add_tool_result(self, tool_call_id: str, name: str, result: str) -> None:
        self.messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": name,
                "content": result,
            }
        )

    # ---------- rendering ----------

    def for_api(self) -> list[dict[str, Any]]:
        """Compact via the engine (if it wants) then prepend the system prompt."""
        if self.engine.should_compress(self.messages):
            self.messages = self.engine.compress(self.messages)
        return [{"role": "system", "content": self.system_prompt}] + self.messages

    # ---------- diagnostics ----------

    def token_estimate(self) -> int:
        from .context_engine import total_tokens, approx_tokens
        return total_tokens(self.messages) + approx_tokens(
            {"content": self.system_prompt}
        )

    def stats(self) -> dict[str, Any]:
        return {
            "engine": self.engine.name,
            "messages": len(self.messages),
            "tokens_est": self.token_estimate(),
            "threshold": self.engine.threshold_tokens,
            "compressions": self.engine.compression_count,
        }
