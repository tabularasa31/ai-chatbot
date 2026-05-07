"""LLM-unavailable degraded state — classification and Pydantic contract.

When the OpenAI provider is unreachable (timeout, 5xx, rate-limit, quota
exhausted, invalid key), the chat pipeline must NOT auto-create a support
ticket. Instead it returns a typed degraded outcome so the widget can render
a fallback message with Try again / Contact support buttons.

The classifier maps :class:`backend.core.openai_errors.OpenAIFailureKind`
into a presentation-layer enum that the widget can switch on.
"""

from __future__ import annotations

from enum import Enum

from openai import (
    APIError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import BaseModel

from backend.core.openai_errors import OpenAIFailureKind, classify_openai_error


class LlmFailureType(str, Enum):
    provider_unavailable = "provider_unavailable"
    provider_timeout = "provider_timeout"
    rate_limited = "rate_limited"
    quota_exhausted = "quota_exhausted"
    invalid_api_key = "invalid_api_key"
    unknown_llm_error = "unknown_llm_error"


class LlmFailureState(BaseModel):
    """Widget-facing failure descriptor.

    Emitted in the SSE ``done`` event alongside the localized fallback ``text``.
    Old widgets that ignore this field still render the text — backward-compat
    requirement (AC5).
    """

    type: LlmFailureType
    retryable: bool
    can_escalate: bool = True


_QUOTA_HINTS = ("insufficient_quota", "exceeded your current quota", "billing")


def _is_quota_exhausted(exc: Exception) -> bool:
    """Disambiguate RateLimitError between transient throttling and billing/quota.

    OpenAI returns 429 for both ordinary rate limits and exhausted quota.
    The two have very different UX: rate limits are retryable, quota is not.
    The body / ``code`` field carries the discriminator.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code.lower() == "insufficient_quota":
        return True
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        inner = body.get("error") if isinstance(body.get("error"), dict) else body
        for key in ("code", "type"):
            value = inner.get(key) if isinstance(inner, dict) else None
            if isinstance(value, str) and value.lower() == "insufficient_quota":
                return True
    msg = str(exc).lower()
    return any(hint in msg for hint in _QUOTA_HINTS)


def classify_llm_failure(exc: Exception) -> LlmFailureState:
    """Map an OpenAI exception to a widget-facing :class:`LlmFailureState`."""
    if isinstance(exc, RateLimitError):
        if _is_quota_exhausted(exc):
            return LlmFailureState(
                type=LlmFailureType.quota_exhausted,
                retryable=False,
            )
        return LlmFailureState(
            type=LlmFailureType.rate_limited,
            retryable=True,
        )
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return LlmFailureState(
            type=LlmFailureType.invalid_api_key,
            retryable=False,
        )

    classified = classify_openai_error(exc)
    kind = classified.kind
    if kind is OpenAIFailureKind.TIMEOUT:
        return LlmFailureState(type=LlmFailureType.provider_timeout, retryable=True)
    if kind is OpenAIFailureKind.RATE_LIMIT:
        return LlmFailureState(type=LlmFailureType.rate_limited, retryable=True)
    if kind is OpenAIFailureKind.TRANSIENT:
        return LlmFailureState(type=LlmFailureType.provider_unavailable, retryable=True)
    if kind is OpenAIFailureKind.PERMANENT:
        return LlmFailureState(type=LlmFailureType.invalid_api_key, retryable=False)

    if isinstance(exc, APIError):
        return LlmFailureState(type=LlmFailureType.unknown_llm_error, retryable=True)
    return LlmFailureState(type=LlmFailureType.unknown_llm_error, retryable=False)
