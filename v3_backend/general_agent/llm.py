"""Thin OpenAI-compatible LLM client with classified retry + streaming.

Uses `agent.errors.classify_api_error` to decide whether an exception is
retryable and what backoff to apply. Auth / billing / format errors are
raised immediately as `NonRetryableError` so callers don't burn attempts.

Streaming model (cf. hermes-agent's run_agent.py for the precedent):
- `LLM.complete()` accepts optional `stream_callbacks: StreamCallbacks`
- When set, the call uses OpenAI's `stream=True` mode internally and
  fires per-token callbacks on the way through, while still returning a
  ChatCompletionMessage-compatible object at the end so the agent loop
  is unchanged.
- Tool calls in streaming arrive incrementally (id → name char by char →
  args char by char). We accumulate by `tc_delta.index` per the
  OpenAI streaming spec.
- When tool_calls are present, regular `delta.content` is suppressed
  (avoids "I'll use the tool…" ghost text from chatty models).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from openai import OpenAI, APIError, RateLimitError, APITimeoutError

from .errors import NonRetryableError, classify_api_error, FailoverReason


@dataclass
class LLMConfig:
    model: str = "deepseek-chat"
    base_url: str | None = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout: float = 120.0
    max_retries: int = 3
    retry_backoff: float = 2.0
    # Provider-specific fields the OpenAI SDK doesn't know about, sent
    # via `extra_body` on every `chat.completions.create()` call. Example:
    # Moonshot's `thinking={"type": "disabled"}` to suppress reasoning
    # mode (otherwise the API requires `reasoning_content` to be replayed
    # in every assistant-with-tool-call message — which standard chat
    # history doesn't carry, so the second turn fails with HTTP 400).
    extra_body: dict[str, Any] | None = None


@dataclass
class StreamCallbacks:
    """Per-token hooks fired during a streaming completion.

    All callbacks are invoked synchronously from whichever thread is
    iterating the OpenAI stream. If the host application needs to
    forward these to an asyncio loop (e.g. for SSE), it must do the
    thread-safe handoff itself (see `apps/http/_chat_streams.py` in
    v3_backend for an example using `loop.call_soon_threadsafe`).

    Why separate hooks for text / reasoning / tool calls:
      Visual elements in the UI live in different containers — the main
      bubble, a reasoning <details>, and tool cards. Conflating them in
      a single "delta" callback would require the consumer to re-classify
      every token. Splitting them at this layer keeps consumers simple.
    """

    # Plain assistant text — only fires when no tool_calls are active for
    # the current turn (matches hermes' tool-text suppression rule).
    on_text_delta: Callable[[str], None] | None = None

    # `<reasoning>` / `delta.reasoning_content` style chain-of-thought tokens.
    # Some providers (DeepSeek R1, OpenAI o-series) emit these as a separate
    # field. Routing them here lets the UI render in a thinking-collapsed area.
    on_reasoning_delta: Callable[[str], None] | None = None

    # Fired exactly once per tool_call when its name is first known.
    # Args: (tool_call_id, tool_name)
    on_tool_call_started: Callable[[str, str], None] | None = None

    # Fired for every chunk of a tool_call's `arguments` JSON.
    # Args: (tool_call_id, args_delta_text)
    on_tool_args_delta: Callable[[str, str], None] | None = None

    # Fired when the assistant message (this run iteration) finishes.
    # The agent loop will follow up with tool dispatch + a fresh assistant
    # turn, so this is a natural "segment boundary" for the UI.
    on_assistant_message_done: Callable[[], None] | None = None

    # Fired once at the start of `Agent.run(task)` when one or more SKILL.md
    # files match the user's task text. The payload is a list of
    # `{name, description}` dicts so the UI can render an "activated skill"
    # chip with a hover-tooltip describing the playbook in effect.
    # Not a streaming callback in the strictest sense (skills are matched
    # before the LLM call), but routed through the same callbacks struct
    # because it's the existing SSE bridge in v3.
    on_skills_activated: Callable[[list[dict[str, str]]], None] | None = None


class LLM:
    def __init__(
        self,
        config: LLMConfig | None = None,
        on_usage: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """`on_usage` is called after every successful completion with the
        response's token-usage dict (prompt_tokens/completion_tokens/...).
        Used by the Agent's budget tracker."""
        self.config = config or LLMConfig()
        key = os.environ.get(self.config.api_key_env)
        if not key:
            raise RuntimeError(
                f"Missing {self.config.api_key_env}. Set it in .env or environment."
            )
        kwargs: dict[str, Any] = {"api_key": key, "timeout": self.config.timeout}
        if self.config.base_url:
            kwargs["base_url"] = self.config.base_url
        self.client = OpenAI(**kwargs)
        self.on_usage = on_usage

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        stream_callbacks: StreamCallbacks | None = None,
        **overrides: Any,
    ) -> Any:
        """Make a Chat Completion with classified retry. Returns the message object.

        If `stream_callbacks` is provided, internally uses OpenAI's
        `stream=True` mode and fires per-token callbacks; the return
        value is still a ChatCompletionMessage-compatible object so
        existing callers don't have to branch.

        Retries are classified-aware (RateLimitError / APITimeoutError /
        retryable APIError) and skip non-retryable ones (auth / billing /
        format) immediately.
        """
        params: dict[str, Any] = {
            "model": overrides.get("model", self.config.model),
            "messages": messages,
            "temperature": overrides.get("temperature", self.config.temperature),
            "max_tokens": overrides.get("max_tokens", self.config.max_tokens),
        }
        if tools:
            params["tools"] = tools
            params["tool_choice"] = tool_choice
            # Force sequential tool calls — Gemini's `/v1beta/openai`
            # compat endpoint occasionally emits malformed parallel calls
            # (concatenated function names + JSON args). One-at-a-time
            # avoids the bug at a small latency cost (turns instead of
            # batches). Standard OpenAI honors this; Gemini honors it on
            # the OpenAI-compat path. Observed 2026-05-08 with
            # gemini-2.5-flash. Remove if Gemini fixes the upstream bug.
            params["parallel_tool_calls"] = False
        if self.config.extra_body:
            params["extra_body"] = dict(self.config.extra_body)

        last_err: Exception | None = None
        for attempt in range(self.config.max_retries):
            try:
                if stream_callbacks is not None:
                    return self._complete_streaming(params, stream_callbacks)
                resp = self.client.chat.completions.create(**params)
                # Emit usage (for the budget tracker).
                if self.on_usage is not None:
                    usage = getattr(resp, "usage", None)
                    if usage is not None:
                        try:
                            self.on_usage(
                                usage.model_dump()
                                if hasattr(usage, "model_dump")
                                else dict(usage)
                            )
                        except Exception:
                            pass
                return resp.choices[0].message
            except (RateLimitError, APITimeoutError, APIError) as e:
                last_err = e
                classified = classify_api_error(e)
                # Fatal (auth / billing / format / model_not_found): raise now.
                if not classified.retryable:
                    raise NonRetryableError(classified, e) from e
                if attempt == self.config.max_retries - 1:
                    break
                # Category-aware sleep, not a blind exponential.
                sleep_s = max(
                    classified.backoff_seconds,
                    self.config.retry_backoff ** attempt,
                )
                time.sleep(sleep_s)
        assert last_err is not None
        raise last_err

    # ─── Streaming path ──────────────────────────────────────

    def _complete_streaming(
        self,
        params: dict[str, Any],
        cb: StreamCallbacks,
    ) -> Any:
        """Run a streaming chat completion with per-token callbacks.

        Returns a ChatCompletionMessage object (NOT the OpenAI sdk type;
        a SimpleNamespace shaped identically — content / role / tool_calls).
        This way the caller doesn't care whether streaming was used.

        Algorithm (cf. hermes-agent/run_agent.py:4639-4900):
        1. Iterate stream chunks
        2. Accumulate `delta.content`, `delta.reasoning_content` and
           `delta.tool_calls` into module-local buffers
        3. Fire callbacks AS the data arrives:
           - text → on_text_delta (suppressed when tool_calls active —
             avoids "I'll use the tool…" ghost text from chatty models)
           - reasoning → on_reasoning_delta
           - tool_call name → on_tool_call_started (once per tool)
           - tool_call args → on_tool_args_delta
        4. After stream ends, build the final message object and emit
           usage to the budget tracker
        """
        # Ask the API to include usage in the final chunk so the budget
        # tracker still gets accurate token counts.
        params = dict(params)
        params["stream"] = True
        params["stream_options"] = {"include_usage": True}

        stream = self.client.chat.completions.create(**params)

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        # tool_calls accumulator: index → {id, name, args}
        tool_acc: dict[int, dict[str, str]] = {}
        # remember which tool_calls we've already announced (started)
        tool_started: set[int] = set()
        usage_data: Any = None

        for chunk in stream:
            # The final chunk in include_usage mode has empty choices but
            # carries the usage payload.
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                usage_data = usage

            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            # Reasoning content (DeepSeek R1, OpenAI o-series, some others)
            reasoning_text = (
                getattr(delta, "reasoning_content", None)
                or getattr(delta, "reasoning", None)
            )
            if reasoning_text:
                reasoning_parts.append(reasoning_text)
                if cb.on_reasoning_delta is not None:
                    try:
                        cb.on_reasoning_delta(reasoning_text)
                    except Exception:
                        pass

            # Regular text content
            text = getattr(delta, "content", None)
            if text:
                content_parts.append(text)
                # Suppress text streaming when tool_calls are present in
                # this turn — chatty models otherwise leak "I'll use the
                # tool to look that up..." prefixes (hermes' rule).
                if not tool_acc and cb.on_text_delta is not None:
                    try:
                        cb.on_text_delta(text)
                    except Exception:
                        pass

            # Tool call deltas — accumulate by index, fire callbacks
            tool_calls_delta = getattr(delta, "tool_calls", None) or []
            for tc_delta in tool_calls_delta:
                idx = getattr(tc_delta, "index", None)
                if idx is None:
                    idx = 0
                slot = tool_acc.setdefault(
                    idx,
                    {"id": "", "name": "", "args": "", "extra_content": None},
                )

                tc_id = getattr(tc_delta, "id", None)
                if tc_id:
                    slot["id"] = tc_id

                # Capture provider-specific opaque fields (e.g. Gemini 3's
                # `extra_content.google.thought_signature` — the API REQUIRES
                # this on every replay, and stripping it produces 400
                # "Function call is missing a thought_signature in
                # functionCall parts"). The SDK exposes it as a direct
                # attribute on each tool_call delta object.
                extras = getattr(tc_delta, "extra_content", None)
                if extras:
                    # Merge if streamed across multiple deltas (defensive —
                    # current Gemini 3 ships the whole signature in one chunk).
                    if slot["extra_content"] is None:
                        slot["extra_content"] = extras
                    elif isinstance(extras, dict) and isinstance(slot["extra_content"], dict):
                        slot["extra_content"] = {**slot["extra_content"], **extras}

                fn = getattr(tc_delta, "function", None)
                if fn is not None:
                    fn_name = getattr(fn, "name", None)
                    fn_args = getattr(fn, "arguments", None)
                    if fn_name:
                        slot["name"] += fn_name
                        # Announce tool start once we have *any* name
                        # (some providers emit it whole, some char by char).
                        if (
                            idx not in tool_started
                            and slot["name"]
                            and slot["id"]
                        ):
                            tool_started.add(idx)
                            if cb.on_tool_call_started is not None:
                                try:
                                    cb.on_tool_call_started(slot["id"], slot["name"])
                                except Exception:
                                    pass
                    if fn_args:
                        slot["args"] += fn_args
                        if cb.on_tool_args_delta is not None and slot["id"]:
                            try:
                                cb.on_tool_args_delta(slot["id"], fn_args)
                            except Exception:
                                pass

        # Stream done — fire end-of-message hook
        if cb.on_assistant_message_done is not None:
            try:
                cb.on_assistant_message_done()
            except Exception:
                pass

        # Emit usage to the budget tracker
        if self.on_usage is not None and usage_data is not None:
            try:
                self.on_usage(
                    usage_data.model_dump()
                    if hasattr(usage_data, "model_dump")
                    else dict(usage_data)
                )
            except Exception:
                pass

        # Build the ChatCompletionMessage-compatible return value. Use
        # the openai SDK types so downstream `.model_dump()` / attribute
        # access still works in Context.add_assistant.
        from openai.types.chat import (
            ChatCompletionMessage,
            ChatCompletionMessageToolCall,
        )
        from openai.types.chat.chat_completion_message_tool_call import Function

        full_text = "".join(content_parts)
        sorted_tools = [tool_acc[i] for i in sorted(tool_acc.keys())]
        if sorted_tools:
            tool_calls = []
            for i, t in enumerate(sorted_tools):
                tc = ChatCompletionMessageToolCall(
                    id=t["id"] or f"call_{i}",
                    type="function",
                    function=Function(name=t["name"], arguments=t["args"] or "{}"),
                )
                # Attach Gemini 3 thought_signature (or other provider extras)
                # so it survives `model_dump(exclude_none=True)` and rides the
                # message back to the API on the next turn.
                if t.get("extra_content"):
                    tc.extra_content = t["extra_content"]
                tool_calls.append(tc)
            return ChatCompletionMessage(
                role="assistant",
                content=full_text or None,
                tool_calls=tool_calls,
            )
        return ChatCompletionMessage(
            role="assistant",
            content=full_text,
        )
