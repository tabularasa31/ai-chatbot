from __future__ import annotations

import httpx

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


def test_classify_rate_limit_error() -> None:
    exc = RateLimitError(
        "rate limited",
        response=_response(429, headers={"retry-after": "2.5"}),
        body=None,
    )

    classified = classify_openai_error(exc)

    assert classified.kind == OpenAIFailureKind.RATE_LIMIT
    assert classified.retry_after_seconds == 2.5
    assert classified.status_code == 429


def test_classify_timeout_connection_transient() -> None:
    timeout_exc = APITimeoutError(request=_request())
    connection_exc = APIConnectionError(request=_request())
    internal_exc = InternalServerError("boom", response=_response(500), body=None)

    assert classify_openai_error(timeout_exc).kind == OpenAIFailureKind.TRANSIENT
    assert classify_openai_error(connection_exc).kind == OpenAIFailureKind.TRANSIENT
    assert classify_openai_error(internal_exc).kind == OpenAIFailureKind.TRANSIENT


def test_classify_authentication_permanent() -> None:
    auth_exc = AuthenticationError("auth", response=_response(401), body=None)
    permission_exc = PermissionDeniedError("nope", response=_response(403), body=None)
    bad_request_exc = BadRequestError("bad", response=_response(400), body=None)

    assert classify_openai_error(auth_exc).kind == OpenAIFailureKind.PERMANENT
    assert classify_openai_error(permission_exc).kind == OpenAIFailureKind.PERMANENT
    assert classify_openai_error(bad_request_exc).kind == OpenAIFailureKind.PERMANENT


def test_classify_unknown_api_error() -> None:
    exc = APIError("weird", request=_request(), body=None)

    classified = classify_openai_error(exc)

    assert classified.kind == OpenAIFailureKind.UNKNOWN
    assert classified.status_code is None


def test_classify_non_openai_exception() -> None:
    classified = classify_openai_error(ValueError("bad value"))

    assert classified.kind == OpenAIFailureKind.PERMANENT
    assert classified.status_code is None


def test_parse_retry_after_missing_returns_none() -> None:
    exc = RateLimitError("rate limited", response=_response(429), body=None)

    assert classify_openai_error(exc).retry_after_seconds is None


def test_parse_retry_after_malformed_returns_none() -> None:
    exc = RateLimitError(
        "rate limited",
        response=_response(429, headers={"retry-after": "abc"}),
        body=None,
    )

    assert classify_openai_error(exc).retry_after_seconds is None
