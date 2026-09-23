from __future__ import annotations

import httpx
import pytest

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)

from backend.core.openai_errors import OpenAIFailureKind, classify_openai_error


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _response(status_code: int, *, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status_code, request=_request(), headers=headers)


@pytest.mark.parametrize(
    "exc,expected_kind",
    [
        (
            RateLimitError("rate limited", response=_response(429, headers={"retry-after": "2.5"}), body=None),
            OpenAIFailureKind.RATE_LIMIT,
        ),
        (APITimeoutError(request=_request()), OpenAIFailureKind.TIMEOUT),
        (APIConnectionError(request=_request()), OpenAIFailureKind.TRANSIENT),
        (InternalServerError("boom", response=_response(500), body=None), OpenAIFailureKind.TRANSIENT),
        (AuthenticationError("auth", response=_response(401), body=None), OpenAIFailureKind.PERMANENT),
        (PermissionDeniedError("nope", response=_response(403), body=None), OpenAIFailureKind.PERMANENT),
        (BadRequestError("bad", response=_response(400), body=None), OpenAIFailureKind.PERMANENT),
        (APIError("weird", request=_request(), body=None), OpenAIFailureKind.UNKNOWN),
        (ValueError("bad value"), OpenAIFailureKind.PERMANENT),
    ],
    ids=[
        "rate-limit",
        "timeout",
        "connection-transient",
        "server-500-transient",
        "authentication-permanent",
        "permission-denied-permanent",
        "bad-request-permanent",
        "unknown-api-error",
        "non-openai-exception",
    ],
)
def test_classify_openai_error_kind(exc, expected_kind) -> None:
    assert classify_openai_error(exc).kind == expected_kind


def test_classify_rate_limit_error_details() -> None:
    exc = RateLimitError(
        "rate limited", response=_response(429, headers={"retry-after": "2.5"}), body=None
    )

    classified = classify_openai_error(exc)

    assert classified.retry_after_seconds == 2.5
    assert classified.status_code == 429


def test_classify_unknown_and_non_openai_have_no_status_code() -> None:
    assert classify_openai_error(APIError("weird", request=_request(), body=None)).status_code is None
    assert classify_openai_error(ValueError("bad value")).status_code is None


@pytest.mark.parametrize(
    "headers",
    [{}, {"retry-after": "abc"}],
    ids=["missing-header", "malformed-header"],
)
def test_parse_retry_after_falls_back_to_none(headers) -> None:
    exc = RateLimitError("rate limited", response=_response(429, headers=headers), body=None)

    assert classify_openai_error(exc).retry_after_seconds is None
