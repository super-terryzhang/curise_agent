"""Section 2 — Architecture: Gemini 3 thought_signature roundtrip.

测试目标：
    Gemini 3 (preview 2026-05) introduced an opaque `thought_signature`
    field on every functionCall — the API rejects subsequent turns with
    HTTP 400 unless that signature is replayed verbatim. The OpenAI-compat
    endpoint surfaces it as `tool_calls[i].extra_content.google.thought_signature`.

    general_agent's malformed-split defensive code (`_split_malformed_tool_calls`
    in core.py) always returns a NEW list — so the identity check downstream
    fires and rebuilds `ctx.messages[-1]["tool_calls"]`. If that rebuild
    drops `extra_content`, every Gemini 3 multi-turn session breaks on
    turn 2. `_tool_call_to_dict` is the helper that preserves it.

为什么必须有：
    Gemini 3 family-wide (`gemini-3-flash-preview`, `gemini-3-pro-preview`,
    `gemini-flash-latest` alias) all reject replays without the signature.
    Verified by direct probe 2026-05-19: HTTP 400 "Function call is missing
    a thought_signature in functionCall parts". The fix is a 2-line preserve
    of `extra_content` during rebuild. This test pins it.
"""

from __future__ import annotations

from general_agent.core import _split_malformed_tool_calls, _tool_call_to_dict


class _FakeFn:
    def __init__(self, name: str, args: str) -> None:
        self.name = name
        self.arguments = args


class _FakeToolCall:
    """Minimal stand-in for openai SDK's pydantic tool_call shape.

    The real SDK exposes `extra_content` as a dynamic attribute when the
    upstream API surfaces vendor-specific fields (Gemini 3 does so via
    `tool_calls[i].extra_content.google.thought_signature`). This shim
    mimics the attribute interface that the rebuild logic relies on.
    """

    def __init__(
        self,
        id: str,
        name: str,
        args: str,
        extra_content: dict | None = None,
    ) -> None:
        self.id = id
        self.type = "function"
        self.function = _FakeFn(name, args)
        if extra_content is not None:
            self.extra_content = extra_content


class _View:
    """Minimal tool registry view: just knows the registered tool names."""

    def __init__(self, names: list[str]) -> None:
        self._names = names

    def names(self) -> list[str]:
        return self._names


def test_tool_call_to_dict_preserves_extra_content() -> None:
    """Unit test the helper directly — extra_content must round-trip."""
    sig = "EpQCCpEC-fake-thought-signature-base64-blob"
    tc = _FakeToolCall(
        id="call_1",
        name="list_my_uploads",
        args='{"has_errors":true}',
        extra_content={"google": {"thought_signature": sig}},
    )

    d = _tool_call_to_dict(tc)

    assert d["id"] == "call_1"
    assert d["type"] == "function"
    assert d["function"]["name"] == "list_my_uploads"
    assert d["function"]["arguments"] == '{"has_errors":true}'
    assert d["extra_content"] == {"google": {"thought_signature": sig}}, (
        "extra_content must survive serialization — Gemini 3's API rejects "
        "the next turn with HTTP 400 if thought_signature is dropped"
    )


def test_tool_call_to_dict_no_extras_when_absent() -> None:
    """For providers that don't emit `extra_content` (Gemini 2.5, Kimi,
    DeepSeek), the dict must NOT carry a stray `extra_content` key —
    that would surface as an unknown field on those APIs."""
    tc = _FakeToolCall(id="call_1", name="query_db", args="{}")

    d = _tool_call_to_dict(tc)

    assert "extra_content" not in d


def test_streaming_path_captures_extra_content(monkeypatch) -> None:
    """Streaming path regression — production failed here in v37.

    `_complete_streaming` accumulates tool_calls from stream chunks into
    a private dict and rebuilds pydantic objects at the end. Before this
    fix the accumulator never looked at `tc_delta.extra_content`, so
    every Gemini-3 streaming call lost its `thought_signature` and the
    NEXT turn was rejected by the API with HTTP 400.

    `apps/http/chat.py` ALWAYS passes `stream_callbacks=` — every prod
    chat request goes through this path. The non-streaming probes that
    validated v37 never exercised it. This test pins the streaming
    accumulator behaviour so the same regression can't recur.
    """
    from types import SimpleNamespace

    from general_agent.llm import LLM, LLMConfig, StreamCallbacks

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-no-network")

    # Build streaming chunks shaped like Gemini 3's actual response:
    # tool_call_delta carries `extra_content.google.thought_signature`.
    sig = "STREAM-SIG-base64-XYZ"

    def _make_chunk(delta_payload):
        return SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(**delta_payload))],
            usage=None,
        )

    fn_delta = SimpleNamespace(name="web_search", arguments='{"q":"tokyo"}')
    tc_delta = SimpleNamespace(
        index=0,
        id="call_abc",
        function=fn_delta,
        extra_content={"google": {"thought_signature": sig}},
    )
    chunks = [
        _make_chunk({"role": "assistant", "tool_calls": [tc_delta]}),
        # Final empty chunk with usage (include_usage=True mode)
        SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(
                model_dump=lambda: {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                }
            ),
        ),
    ]

    class _FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_):
                    return iter(chunks)

    cfg = LLMConfig(
        model="gemini-3-flash-preview",
        base_url="x",
        api_key_env="GOOGLE_API_KEY",
        temperature=0.2,
        max_tokens=100,
        timeout=30,
        max_retries=1,
    )
    llm = LLM(cfg)
    llm.client = _FakeClient()  # bypass real network
    cb = StreamCallbacks()

    msg = llm.complete(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "web_search",
                                                  "description": "x",
                                                  "parameters": {"type": "object"}}}],
        stream_callbacks=cb,
    )

    dumped = msg.model_dump(exclude_none=True)
    assert dumped["tool_calls"][0]["function"]["name"] == "web_search"
    # ★ The bit that production broke on — extra_content must survive
    assert "extra_content" in dumped["tool_calls"][0]
    assert (
        dumped["tool_calls"][0]["extra_content"]["google"]["thought_signature"]
        == sig
    )


def test_split_concatenated_propagates_signature_to_all_splits() -> None:
    """Gemini OpenAI-compat sometimes emits a parallel call concatenated:
    name='web_searchweb_searchweb_search', args='{...}{...}{...}'. We
    split it into N synthetic tool_calls so dispatch can call each tool
    once. On the NEXT turn's API replay, Gemini 3 requires every
    function call to carry `thought_signature`; the original combined
    call had exactly ONE signature.

    Empirically (prod 2026-05-19 streaming probe) Gemini 3 accepts the
    same signature DUPLICATED across all N split calls and answers
    normally. Splits without signature → HTTP 400. This test pins the
    duplication behavior.
    """
    sig = {"google": {"thought_signature": "CONCAT-SIG"}}

    class _Fn:
        def __init__(self, name, arguments):
            self.name = name
            self.arguments = arguments

    class _RealToolCall:
        """Mimics the openai SDK pydantic shape with extra_content attr."""
        def __init__(self):
            self.id = "orig"
            self.type = "function"
            self.function = _Fn(
                "tool_atool_btool_c",
                '{"q":"a"}{"q":"b"}{"q":"c"}',
            )
            self.extra_content = sig

    view = _View(names=["tool_a", "tool_b", "tool_c"])

    out = _split_malformed_tool_calls([_RealToolCall()], view)

    assert len(out) == 3, "should split tool_atool_btool_c into 3"
    # ★ Every split must carry the ORIGINAL signature
    for i, tc in enumerate(out):
        assert getattr(tc, "extra_content", None) == sig, (
            f"split[{i}] missing thought_signature — Gemini 3 will reject "
            f"the next turn's API call (production 2026-05-19 incident)"
        )

    # And the dict serialization must include it on every split too
    serialized = [_tool_call_to_dict(tc) for tc in out]
    for i, d in enumerate(serialized):
        assert d["extra_content"] == sig, (
            f"serialized split[{i}] dropped signature in _tool_call_to_dict"
        )


def test_split_pass_through_preserves_signature() -> None:
    """End-to-end: a normal (un-mangled) Gemini 3 tool_call with
    extra_content must survive the `_split_malformed_tool_calls` pass-
    through path AND the subsequent rebuild dict serialization."""
    sig = "REAL-LOOKING-SIG"
    tc = _FakeToolCall(
        id="x",
        name="known_tool",
        args="{}",
        extra_content={"google": {"thought_signature": sig}},
    )
    view = _View(names=["known_tool"])

    input_calls = [tc]
    out = _split_malformed_tool_calls(input_calls, view)

    # _split returns a NEW list (intentional — drives the rebuild downstream)
    assert out is not input_calls
    assert len(out) == 1
    # The tc object passed through unchanged → signature is still on it
    assert out[0].extra_content == {"google": {"thought_signature": sig}}

    # Now serialise as core.py does
    rebuilt = [_tool_call_to_dict(t) for t in out]
    assert rebuilt[0]["extra_content"]["google"]["thought_signature"] == sig
