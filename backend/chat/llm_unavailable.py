"""LLM-unavailable degraded state — classification and Pydantic contract.

When the OpenAI provider is unreachable (timeout, 5xx, rate-limit, quota
exhausted, invalid key), the chat pipeline must NOT auto-create a support
ticket. Instead it returns a typed degraded outcome so the widget can render
a fallback message with Try again / Contact support buttons.

The classifier maps :class:`backend.core.openai_errors.OpenAIFailureKind`
into a presentation-layer enum that the widget can switch on.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING

from openai import (
    APIError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import BaseModel

from backend.chat.language import async_localize_text_to_language_result
from backend.core.db import run_sync
from backend.core.openai_errors import OpenAIFailureKind, classify_openai_error

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import Session

    from backend.models import Tenant

_log = logging.getLogger(__name__)

#: Shared across every "no OpenAI key on the tenant" 400 response.
OPENAI_KEY_NOT_CONFIGURED_MESSAGE = (
    "OpenAI API key not configured. Add your key in dashboard settings."
)


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


def _notify_quota_exceeded(tenant: Tenant, db: Session) -> str:
    """Log the quota-exceeded event to Sentry and return the canonical
    (English) user-facing error detail string (includes support email if
    known). Runs inside a ``run_sync`` greenlet on the event loop thread, so
    it must not make provider calls — the caller localizes the returned text
    via ``async_localize_text_to_language_result``.
    """
    from backend.models import TenantProfile

    _log.error(
        "openai_quota_exceeded: tenant_id=%s tenant_name=%s",
        tenant.id,
        tenant.name,
    )
    try:
        import sentry_sdk

        with sentry_sdk.new_scope() as scope:
            scope.set_tag("error_kind", "openai_quota_exceeded")
            scope.set_context("tenant", {"tenant_id": str(tenant.id), "tenant_name": tenant.name})
            sentry_sdk.capture_message(
                f"OpenAI quota exceeded for tenant '{tenant.name}'",
                level="error",
                scope=scope,
            )
    except Exception:
        pass

    profile = db.get(TenantProfile, tenant.id)
    support_email: str | None = profile.support_email if profile else None
    contact = f" at {support_email}" if support_email else ""
    return (
        "We're currently experiencing technical difficulties and are unable to respond via chat. "
        f"We apologize for the inconvenience — please contact our support team{contact} by email."
    )


async def quota_exceeded_detail(
    tenant: Tenant, db: AsyncSession, *, lang: str, api_key: str | None
) -> str:
    """Sentry notification + localized user-facing detail for the 402 path.

    Shared by the chat and widget routes so quota exhaustion produces the
    same message and side effect regardless of entry point.
    """
    canonical = await run_sync(db, lambda s: _notify_quota_exceeded(tenant, s))
    result = await async_localize_text_to_language_result(
        canonical_text=canonical,
        target_language=lang,
        api_key=api_key,
    )
    return result.text
