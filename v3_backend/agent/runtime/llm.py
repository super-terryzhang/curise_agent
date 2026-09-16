"""LLM configuration for v3's chat agent.

general-agent's `LLMConfig` is provider-agnostic — it just needs an
OpenAI-compatible endpoint plus a model name and an env var holding the
API key. Switching providers is therefore a routing concern: pick the
right `base_url` + `api_key_env` for the requested model and feed them
into `LLMConfig`.

Supported providers (all OpenAI Chat Completions compatible):
- Gemini via Google's first-party endpoint (default, uses `GOOGLE_API_KEY`)
- Kimi (Moonshot) via `api.moonshot.ai/v1` (uses `MOONSHOT_API_KEY`)
- DeepSeek via `api.deepseek.com` (uses `DEEPSEEK_API_KEY`)

Routing is driven by the model-name prefix (e.g. `gemini-` → gemini,
`kimi-`/`moonshot-` → moonshot, `deepseek-` → deepseek). The default
model lives in `settings.AGENT_CHAT_MODEL`; callers may pass an explicit
`model=` to override per-request (e.g. session/user picked Kimi).

Why not the native `google-genai` SDK:
- Would require a custom `LLM` class in general-agent (forking the
  abstraction), defeating the migration's whole point.
- Native SDK's tool-call shape differs from OpenAI's, breaking message
  round-tripping with `V3SessionStore` (which stores OpenAI Chat
  Completions schema verbatim).

Native SDK is still appropriate for one-shot non-conversational tasks
(PO extraction, schema-first PDF parsing) where Gemini-only features
like JSON mode + thinking budget pay for themselves. Those paths
already use `google-genai` directly and we keep them as-is.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from general_agent import LLMConfig

from infrastructure.config import settings

logger = logging.getLogger(__name__)

# ─── Provider registry ────────────────────────────────────────────
# Each entry maps an OpenAI-compatible chat provider to the endpoint
# and the env-var name holding its API key. general-agent reads the
# env var at runtime via `LLMConfig.api_key_env`, so we never embed
# secrets here.

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_API_KEY_ENV = "GOOGLE_API_KEY"

# NOTE: Moonshot has two separate consoles + endpoints:
#   .cn (platform.moonshot.cn, RMB billing) → api.moonshot.cn/v1
#   .ai (platform.moonshot.ai, USD billing) → api.moonshot.ai/v1
# A key from one console is rejected by the other with HTTP 401. Our prod
# key (`v3-moonshot-api-key` secret) was provisioned on the .cn console,
# and that's also where `kimi-k2.6` lives — verified 2026-05-14 via curl.
MOONSHOT_BASE_URL = "https://api.moonshot.cn/v1"
MOONSHOT_API_KEY_ENV = "MOONSHOT_API_KEY"

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"


@dataclass(frozen=True)
class _Provider:
    name: str
    base_url: str
    api_key_env: str
    prefixes: tuple[str, ...]
    # Provider-specific extras sent via OpenAI SDK's `extra_body`. None
    # means "no extras." Used for Moonshot's `thinking={"type":"disabled"}`
    # toggle — see the per-provider definition below for why.
    extra_body: tuple[tuple[str, object], ...] = ()  # tuple-of-pairs for frozen-dc hashability
    # Optional pinned temperature. Some Moonshot reasoning models reject
    # any value other than a single fixed point (e.g. `kimi-k2.6` with
    # thinking disabled requires exactly 0.6 — verified prod 2026-05-14).
    # None = use the LLMConfig default.
    temperature: float | None = None


_PROVIDERS: tuple[_Provider, ...] = (
    _Provider(
        name="gemini",
        base_url=GEMINI_OPENAI_BASE_URL,
        api_key_env=GEMINI_API_KEY_ENV,
        prefixes=("gemini-", "gemini/"),
    ),
    _Provider(
        name="moonshot",
        base_url=MOONSHOT_BASE_URL,
        api_key_env=MOONSHOT_API_KEY_ENV,
        prefixes=("kimi-", "moonshot-", "kimi/", "moonshot/"),
        # `kimi-k2.6` + thinking-disabled rejects any temperature other
        # than 0.6 exactly: "invalid temperature: only 0.6 is allowed for
        # this model" (verified prod 2026-05-14 — `kimi-k2.5` shares the
        # constraint; `moonshot-v1-*` does NOT, but we only expose kimi-*
        # in the picker today so pinning is correct).
        temperature=0.6,
        # `kimi-k2.6` (and other K-series) defaults to "thinking" mode:
        # the API returns `reasoning_content` alongside `content`/`tool_calls`,
        # and on subsequent turns REQUIRES `reasoning_content` to be replayed
        # on every assistant-with-tool-call message. Standard OpenAI chat
        # history (what our V3SessionStore persists, and what general_agent
        # rebuilds turn-to-turn) doesn't carry that field — so the second
        # turn always fails:
        #
        #   400 invalid_request_error: "thinking is enabled but
        #   reasoning_content is missing in assistant tool call message
        #   at index N"
        #
        # Verified prod incident 2026-05-14. The fix is to disable thinking
        # at the API call level: `thinking={"type":"disabled"}` makes
        # Moonshot accept replays without `reasoning_content` while keeping
        # tool-calling intact. (Other shapes that don't work: `thinking:false`
        # → "expected type object"; `thinking:{enabled:false}` → 400; only
        # `{"type":"disabled"}` is honored — verified via curl 2026-05-14.)
        extra_body=(("thinking", {"type": "disabled"}),),
    ),
    _Provider(
        name="deepseek",
        base_url=DEEPSEEK_BASE_URL,
        api_key_env=DEEPSEEK_API_KEY_ENV,
        prefixes=("deepseek-", "deepseek/"),
    ),
)

# Default provider used when the model name doesn't match any known
# prefix (covers Gemini's bare model names like `gemini-2.5-flash`).
_DEFAULT_PROVIDER = _PROVIDERS[0]

# Known-bad → known-good aliases. Used to rescue sessions that stored a
# model name we no longer publish (e.g. `kimi-k2-0905-preview` was a v23
# label for the .ai console — when we moved to the .cn console in v24
# that id 404s, leaving sessions silently broken). Aliases keep them
# working while we backfill the DB. Keep the list short — long-term
# stale rows should be migrated.
_MODEL_ALIASES: dict[str, str] = {
    "kimi-k2-0905-preview": "kimi-k2.6",
    "kimi-k2-turbo-preview": "kimi-k2.6",
}


def _resolve_provider(model: str) -> _Provider:
    """Pick the OpenAI-compat endpoint + key env for a model name.

    Match is by lowercase prefix. Unknown names fall through to the
    default provider (Gemini) — this keeps the contract stable for
    callers who pass through env-supplied model names verbatim.
    """
    lowered = model.strip().lower()
    for provider in _PROVIDERS:
        for prefix in provider.prefixes:
            if lowered.startswith(prefix):
                return provider
    return _DEFAULT_PROVIDER


def default_chat_llm_config(*, model: str | None = None) -> LLMConfig:
    """Build an `LLMConfig` for the chat agent.

    `model` overrides `settings.AGENT_CHAT_MODEL` for per-call switching
    (e.g. UI lets the user pick Kimi for one session). When omitted, we
    use the settings default — `gemini-2.5-flash` unless overridden by
    env. Provider routing (base_url + api_key_env) is derived from the
    resolved model name via `_resolve_provider`.

    Stale model names in `_MODEL_ALIASES` are rewritten before lookup so
    sessions persisted under a prior catalogue don't 404 forever.
    """
    requested = model or settings.AGENT_CHAT_MODEL
    resolved_model = _MODEL_ALIASES.get(requested, requested)
    if resolved_model != requested:
        logger.warning(
            "chat: aliasing stale model %r → %r (session DB has an outdated id)",
            requested,
            resolved_model,
        )
    provider = _resolve_provider(resolved_model)
    return LLMConfig(
        model=resolved_model,
        base_url=provider.base_url,
        api_key_env=provider.api_key_env,
        temperature=provider.temperature if provider.temperature is not None else 0.2,
        max_tokens=8192,
        timeout=120.0,
        max_retries=3,
        retry_backoff=2.0,
        extra_body=dict(provider.extra_body) if provider.extra_body else None,
    )
