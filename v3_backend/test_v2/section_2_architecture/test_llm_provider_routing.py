"""Section 2 — Architecture: chat LLM provider routing.

测试目标：
    `agent.runtime.llm._resolve_provider` 必须根据模型名前缀稳定地
    挑出 (base_url, api_key_env) 组合。这是用户在前端切换 Gemini /
    Kimi / DeepSeek 时背后唯一的路由层 —— 选错就直接 401 或
    "model not found"。

为什么必须有：
    Prod 2026-05-13 起 chat 默认 Gemini，但 Gemini OpenAI-compat
    端点已确认会幻觉 `parse_file` 工具调用并伪造 "denied by policy"
    错误（Google AI Forum + gh:google-gemini/gemini-cli#813）。Kimi
    K2 是 fallback。如果未来有人把 prefix 写错或加新 provider 没
    挂上 prefixes，路由就静默回落到 default(gemini)，用户的 Kimi 选
    择被忽略 —— 这条测试就是用来阻止这种 silent regression。

设计方法：
    - 纯函数测试，零 fixture。
    - 不真打 LLM；只断 LLMConfig 字段。
"""

from __future__ import annotations

import pytest

from agent.runtime.llm import (
    DEEPSEEK_API_KEY_ENV,
    DEEPSEEK_BASE_URL,
    GEMINI_API_KEY_ENV,
    GEMINI_OPENAI_BASE_URL,
    MOONSHOT_API_KEY_ENV,
    MOONSHOT_BASE_URL,
    _resolve_provider,
    default_chat_llm_config,
)


@pytest.mark.parametrize(
    "model_name, expected_base_url, expected_key_env",
    [
        # ─── Gemini family ────────────────────────────────────
        ("gemini-2.5-flash", GEMINI_OPENAI_BASE_URL, GEMINI_API_KEY_ENV),
        ("gemini-2.5-pro", GEMINI_OPENAI_BASE_URL, GEMINI_API_KEY_ENV),
        ("gemini-1.5-flash-002", GEMINI_OPENAI_BASE_URL, GEMINI_API_KEY_ENV),
        # ─── Kimi / Moonshot ──────────────────────────────────
        # Dotted names like `kimi-k2.6` are the actual model IDs on the
        # .cn console — confirmed via `GET /v1/models` on 2026-05-14.
        # The prefix matcher must catch them despite the dot.
        ("kimi-k2.6", MOONSHOT_BASE_URL, MOONSHOT_API_KEY_ENV),
        ("kimi-k2.5", MOONSHOT_BASE_URL, MOONSHOT_API_KEY_ENV),
        ("kimi-k2-0905-preview", MOONSHOT_BASE_URL, MOONSHOT_API_KEY_ENV),
        ("kimi-k2-turbo-preview", MOONSHOT_BASE_URL, MOONSHOT_API_KEY_ENV),
        ("moonshot-v1-8k", MOONSHOT_BASE_URL, MOONSHOT_API_KEY_ENV),
        ("moonshot-v1-128k", MOONSHOT_BASE_URL, MOONSHOT_API_KEY_ENV),
        # ─── DeepSeek ─────────────────────────────────────────
        ("deepseek-chat", DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY_ENV),
        ("deepseek-reasoner", DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY_ENV),
        # ─── Unknown → default (gemini) — backward-compat ────
        ("totally-made-up-model", GEMINI_OPENAI_BASE_URL, GEMINI_API_KEY_ENV),
        ("", GEMINI_OPENAI_BASE_URL, GEMINI_API_KEY_ENV),
    ],
)
def test_resolve_provider_picks_right_endpoint(
    model_name: str, expected_base_url: str, expected_key_env: str
) -> None:
    provider = _resolve_provider(model_name)
    assert provider.base_url == expected_base_url
    assert provider.api_key_env == expected_key_env


def test_resolve_provider_case_insensitive() -> None:
    """Prefix match must be case-insensitive — UI may pass 'KIMI-K2' or
    'Gemini-2.5-Flash' depending on how it tracks the user selection."""
    assert _resolve_provider("KIMI-K2-0905").api_key_env == MOONSHOT_API_KEY_ENV
    assert _resolve_provider("Gemini-2.5-Flash").api_key_env == GEMINI_API_KEY_ENV
    assert _resolve_provider(" gemini-2.5-flash ").api_key_env == GEMINI_API_KEY_ENV


def test_default_chat_llm_config_uses_resolved_provider_for_kimi() -> None:
    """Per-call `model=` override → LLMConfig carries Moonshot endpoint
    + MOONSHOT_API_KEY env. Settings default doesn't get in the way.

    We pin the .cn host explicitly here: our prod API key was provisioned
    on `platform.moonshot.cn` and is rejected by `api.moonshot.ai` with
    HTTP 401 (verified prod incident 2026-05-14). The two consoles do
    NOT share credentials — flipping the base URL silently breaks Kimi
    chat sessions.
    """
    cfg = default_chat_llm_config(model="kimi-k2.6")
    assert cfg.model == "kimi-k2.6"
    assert cfg.base_url == MOONSHOT_BASE_URL
    assert cfg.base_url == "https://api.moonshot.cn/v1"
    assert cfg.api_key_env == MOONSHOT_API_KEY_ENV


def test_moonshot_config_pins_temperature_for_reasoning_models() -> None:
    """Kimi K2.x (kimi-k2.5, kimi-k2.6) reject any temperature value
    other than a single fixed point — the API responds 400 with
    `"invalid temperature: only 0.6 is allowed for this model"` (when
    thinking is disabled). Our default temperature is 0.2 which would
    fail every Kimi call. Provider config pins it. Verified prod
    incident 2026-05-14."""
    cfg = default_chat_llm_config(model="kimi-k2.6")
    assert cfg.temperature == 0.6
    cfg = default_chat_llm_config(model="kimi-k2.5")
    assert cfg.temperature == 0.6


def test_gemini_config_keeps_default_temperature() -> None:
    """Gemini doesn't pin temperature; we keep the LLMConfig default."""
    cfg = default_chat_llm_config(model="gemini-2.5-flash")
    assert cfg.temperature == 0.2


def test_moonshot_config_disables_thinking_mode() -> None:
    """Moonshot's `kimi-*` reasoning models default to thinking mode,
    which makes their API reject the second-turn replay unless every
    prior tool-call assistant message carries `reasoning_content` —
    a field standard OpenAI chat history (what our V3SessionStore
    persists, what general_agent rebuilds) doesn't carry, so the agent
    deadlocks after Turn 1 with a 400 Bad Request:

      "thinking is enabled but reasoning_content is missing in
       assistant tool call message at index N"

    Production incident 2026-05-14. Fix: pass extra_body={"thinking":
    {"type": "disabled"}} on every Moonshot request — verified to make
    the API accept replays without reasoning_content (curl 2026-05-14).
    """
    cfg = default_chat_llm_config(model="kimi-k2.6")
    assert cfg.extra_body == {"thinking": {"type": "disabled"}}, (
        f"Moonshot LLMConfig must disable thinking, got {cfg.extra_body!r}"
    )


def test_gemini_config_has_no_extra_body() -> None:
    """Gemini doesn't need provider-specific extras; extra_body stays
    None so we don't leak Moonshot's `thinking` flag into Gemini's
    OpenAI-compat endpoint (which rejects unknown body fields)."""
    cfg = default_chat_llm_config(model="gemini-2.5-flash")
    assert cfg.extra_body is None


def test_default_chat_llm_config_aliases_stale_kimi_name() -> None:
    """Sessions stored with `kimi-k2-0905-preview` (a v23-era id from
    when we routed to api.moonshot.ai) must keep working after the v24
    switch to api.moonshot.cn — the alias map rewrites the id to
    `kimi-k2.6` before dispatch. Without this, those sessions silently
    404 (model not found on .cn). Prod incident 2026-05-14."""
    cfg = default_chat_llm_config(model="kimi-k2-0905-preview")
    assert cfg.model == "kimi-k2.6"
    assert cfg.base_url == MOONSHOT_BASE_URL


def test_default_chat_llm_config_falls_back_to_settings() -> None:
    """No explicit model → use `settings.AGENT_CHAT_MODEL` and route
    accordingly. We assert it routes to Gemini because that's the prod
    default (`gemini-2.5-flash`); if a future migration changes the
    default, update this test deliberately."""
    cfg = default_chat_llm_config()
    assert cfg.model  # non-empty
    # Default settings → gemini-* → Gemini OpenAI-compat endpoint.
    assert cfg.base_url == GEMINI_OPENAI_BASE_URL
    assert cfg.api_key_env == GEMINI_API_KEY_ENV
