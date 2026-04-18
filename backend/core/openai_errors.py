"""Error classification for OpenAI SDK exceptions.

Classification drives retry decisions across both user-facing endpoints and
background jobs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)


class OpenAIFailureKind(str, Enum):
    TRANSIENT = "transient"
    RATE_LIMIT = "rate_limit"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ClassifiedError:
    kind: OpenAIFailureKind
    retry_after_seconds: float | None
    status_code: int | None
    message: str


def classify_openai_error(exc: Exception) -> ClassifiedError:
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    retry_after = _parse_retry_after(exc)
    message = str(exc)[:500]

    if isinstance(exc, RateLimitError) or status == 429:
        return ClassifiedError(OpenAIFailureKind.RATE_LIMIT, retry_after, status, message)
    if isinstance(exc, (APIConnectionError, APITimeoutError, InternalServerError)):
        return ClassifiedError(OpenAIFailureKind.TRANSIENT, retry_after, status, message)
    if isinstance(exc, APIError) and status in {500, 502, 503, 504}:
        return ClassifiedError(OpenAIFailureKind.TRANSIENT, retry_after, status, message)
    if isinstance(
        exc,
        (AuthenticationError, PermissionDeniedError, NotFoundError, BadRequestError),
    ):
        return ClassifiedError(OpenAIFailureKind.PERMANENT, None, status, message)
    if isinstance(exc, APIError):
        return ClassifiedError(OpenAIFailureKind.UNKNOWN, retry_after, status, message)
    return ClassifiedError(OpenAIFailureKind.PERMANENT, None, None, message)


def _parse_retry_after(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    if response is None:
        return None
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
