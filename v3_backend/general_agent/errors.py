"""Error classification + retry policy for LLM calls.

Distilled from hermes-agent/agent/error_classifier.py (809 LOC → ~180 LOC).

Why this matters: the old `LLM.complete()` retried on ANY `APIError`, which
means a 401 (bad API key) burned 3 attempts × 2^n backoff before failing,
and a 429 retried with the same short delay as a transient timeout. This
module classifies each error into a category with a per-category recovery
hint so the retry loop can behave sensibly.

Hermes categories we mirror (minus some provider-specific ones):
    auth               — 401/403, bad key. Non-retryable. Abort fast.
    billing            — 402, "insufficient_quota". Non-retryable. Abort.
    rate_limit         — 429, "too many requests". Retryable, long backoff.
    overloaded         — 503/529. Retryable, medium backoff.
    server_error       — 500/502. Retryable, short backoff.
    timeout            — connection/read timeout. Retryable, short backoff.
    context_overflow   — prompt too long. Non-retryable here (caller should
                         compress and try again).
    payload_too_large  — 413. Non-retryable here.
    format_error       — 400 bad request. Non-retryable (agent bug).
    model_not_found    — 404 / invalid model. Non-retryable.
    unknown            — catch-all. Retryable with short backoff.

Hermes skipped: `thinking_signature`, `long_context_tier` (Anthropic-specific),
credential rotation (we don't pool keys).
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any


log = logging.getLogger(__name__)


class FailoverReason(enum.Enum):
    auth = "auth"
    billing = "billing"
    rate_limit = "rate_limit"
    overloaded = "overloaded"
    server_error = "server_error"
    timeout = "timeout"
    context_overflow = "context_overflow"
    payload_too_large = "payload_too_large"
    format_error = "format_error"
    model_not_found = "model_not_found"
    unknown = "unknown"


@dataclass
class ClassifiedError:
    reason: FailoverReason
    status_code: int | None = None
    message: str = ""
    retryable: bool = True
    # Suggested delay BEFORE the next retry, in seconds.
    backoff_seconds: float = 2.0
    # If True, the agent should try to compress context before retrying.
    should_compress: bool = False


# Pattern tables (lower-case substring matching).
_RATE_LIMIT = (
    "rate limit", "rate_limit", "too many requests", "throttled",
    "requests per minute", "tokens per minute", "try again in",
    "please retry after", "resource_exhausted",
)

_BILLING = (
    "insufficient credits", "insufficient_quota", "credit balance",
    "credits have been exhausted", "top up your credits",
    "payment required", "exceeded your current quota",
    "account is deactivated",
)

_AUTH = (
    "invalid api key", "invalid_api_key", "authentication",
    "unauthorized", "forbidden", "invalid token", "token expired",
    "token revoked", "access denied",
)

_CONTEXT_OVERFLOW = (
    "context length", "context size", "maximum context", "token limit",
    "too many tokens", "reduce the length", "exceeds the limit",
    "context window", "prompt is too long",
    "超过最大长度", "上下文长度",
)

_PAYLOAD_TOO_LARGE = (
    "request entity too large", "payload too large", "error code: 413",
)

_MODEL_NOT_FOUND = (
    "is not a valid model", "invalid model", "model not found",
    "model_not_found", "does not exist", "no such model",
    "unknown model", "unsupported model",
)

_TRANSPORT_TYPES = frozenset({
    "APIConnectionError", "APITimeoutError",
    "ReadTimeout", "ConnectTimeout", "PoolTimeout",
    "ConnectError", "RemoteProtocolError",
    "ConnectionError", "ConnectionResetError",
    "TimeoutError", "ReadError", "ServerDisconnectedError",
})


def _status_code(error: Exception) -> int | None:
    """Best-effort extraction of HTTP status code from an exception.

    OpenAI SDK errors put it on `.status_code`. Other SDKs put it on
    `.response.status_code`. Fall back to None.
    """
    code = getattr(error, "status_code", None)
    if isinstance(code, int):
        return code
    resp = getattr(error, "response", None)
    if resp is not None:
        code = getattr(resp, "status_code", None)
        if isinstance(code, int):
            return code
    return None


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(p in text for p in patterns)


def classify_api_error(error: Exception) -> ClassifiedError:
    """Map an exception to a ClassifiedError with retry hints.

    Priority:
      1. Transport-layer exception type → timeout
      2. Status code (if present) + message-aware refinement
      3. Message-pattern matching (billing > rate_limit > context > auth)
      4. Fallback: unknown + retryable
    """
    msg = str(error).lower()
    code = _status_code(error)
    err_type = type(error).__name__

    # 1. Transport — distinct because the socket layer fails before HTTP exists.
    if err_type in _TRANSPORT_TYPES:
        return ClassifiedError(
            reason=FailoverReason.timeout,
            message=str(error),
            retryable=True,
            backoff_seconds=2.0,
        )

    # 2. Status-code-driven.
    if code is not None:
        if code in (401, 403):
            return ClassifiedError(
                reason=FailoverReason.auth,
                status_code=code,
                message=str(error),
                retryable=False,
            )
        if code == 402 or _matches(msg, _BILLING):
            return ClassifiedError(
                reason=FailoverReason.billing,
                status_code=code,
                message=str(error),
                retryable=False,
            )
        if code == 404 or _matches(msg, _MODEL_NOT_FOUND):
            return ClassifiedError(
                reason=FailoverReason.model_not_found,
                status_code=code,
                message=str(error),
                retryable=False,
            )
        if code == 413 or _matches(msg, _PAYLOAD_TOO_LARGE):
            return ClassifiedError(
                reason=FailoverReason.payload_too_large,
                status_code=code,
                message=str(error),
                retryable=False,
                should_compress=True,
            )
        if code == 429:
            return ClassifiedError(
                reason=FailoverReason.rate_limit,
                status_code=code,
                message=str(error),
                retryable=True,
                backoff_seconds=10.0,
            )
        if code in (503, 529):
            return ClassifiedError(
                reason=FailoverReason.overloaded,
                status_code=code,
                message=str(error),
                retryable=True,
                backoff_seconds=5.0,
            )
        if 500 <= code < 600:
            return ClassifiedError(
                reason=FailoverReason.server_error,
                status_code=code,
                message=str(error),
                retryable=True,
                backoff_seconds=3.0,
            )
        if code == 400:
            if _matches(msg, _CONTEXT_OVERFLOW):
                return ClassifiedError(
                    reason=FailoverReason.context_overflow,
                    status_code=code,
                    message=str(error),
                    retryable=False,
                    should_compress=True,
                )
            return ClassifiedError(
                reason=FailoverReason.format_error,
                status_code=code,
                message=str(error),
                retryable=False,
            )

    # 3. No status code — fall back to message patterns (billing first so
    # "exceeded quota" doesn't get mis-bucketed as rate_limit).
    if _matches(msg, _BILLING):
        return ClassifiedError(
            reason=FailoverReason.billing, message=str(error),
            retryable=False,
        )
    if _matches(msg, _AUTH):
        return ClassifiedError(
            reason=FailoverReason.auth, message=str(error),
            retryable=False,
        )
    if _matches(msg, _CONTEXT_OVERFLOW):
        return ClassifiedError(
            reason=FailoverReason.context_overflow, message=str(error),
            retryable=False, should_compress=True,
        )
    if _matches(msg, _RATE_LIMIT):
        return ClassifiedError(
            reason=FailoverReason.rate_limit, message=str(error),
            retryable=True, backoff_seconds=10.0,
        )
    if _matches(msg, _MODEL_NOT_FOUND):
        return ClassifiedError(
            reason=FailoverReason.model_not_found, message=str(error),
            retryable=False,
        )

    # 4. Default.
    return ClassifiedError(
        reason=FailoverReason.unknown, message=str(error),
        retryable=True, backoff_seconds=2.0,
    )


class NonRetryableError(Exception):
    """Raised when the classifier says don't retry. Wraps the original."""

    def __init__(self, classified: ClassifiedError, original: Exception) -> None:
        self.classified = classified
        self.original = original
        super().__init__(
            f"[{classified.reason.value}] {classified.message or str(original)}"
        )
