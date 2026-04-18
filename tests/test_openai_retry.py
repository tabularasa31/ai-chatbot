from __future__ import annotations

import logging

import httpx
import pytest
from openai import APITimeoutError, AuthenticationError, InternalServerError, RateLimitError

from backend.core.openai_retry import _delay_for_user, call_openai_with_retry
from backend.core.openai_errors import ClassifiedError, OpenAIFailureKind


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _response(status_code: int, *, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status_code, request=_request(), headers=headers)


def test_retry_succeeds_after_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}
    sleeps: list[float] = []

    monkeypatch.setattr("backend.core.openai_retry.time.sleep", sleeps.append)

    def _fn() -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            raise APITimeoutError(request=_request())
        return "ok"

    result = call_openai_with_retry("chat_generate", _fn)

    assert result == "ok"
    assert calls["count"] == 2
    assert len(sleeps) == 1


def test_retry_exhausts_then_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.core.openai_retry.time.sleep", lambda _: None)

    with pytest.raises(InternalServerError):
        call_openai_with_retry(
            "chat_generate",
            lambda: (_ for _ in ()).throw(
                InternalServerError("boom", response=_response(500), body=None)
            ),
        )


def test_no_retry_on_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}
    monkeypatch.setattr("backend.core.openai_retry.time.sleep", lambda _: None)

    def _fn() -> str:
        calls["count"] += 1
        raise AuthenticationError("auth", response=_response(401), body=None)

    with pytest.raises(AuthenticationError):
        call_openai_with_retry("chat_generate", _fn)

    assert calls["count"] == 1


def test_rate_limit_honors_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}
    sleeps: list[float] = []
    monkeypatch.setattr("backend.core.openai_retry.time.sleep", sleeps.append)

    def _fn() -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RateLimitError(
                "rate limited",
                response=_response(429, headers={"retry-after": "1"}),
                body=None,
            )
        return "ok"

    assert call_openai_with_retry("chat_generate", _fn) == "ok"
    assert sleeps == [1.0]


def test_rate_limit_long_retry_after_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.core.openai_retry.time.sleep", lambda _: None)

    with pytest.raises(RateLimitError):
        call_openai_with_retry(
            "chat_generate",
            lambda: (_ for _ in ()).throw(
                RateLimitError(
                    "rate limited",
                    response=_response(429, headers={"retry-after": "10"}),
                    body=None,
                )
            ),
        )


def test_budget_cap_prevents_long_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"value": 0.0}

    def _monotonic() -> float:
        return clock["value"]

    def _sleep(delay: float) -> None:
        clock["value"] += delay

    monkeypatch.setattr("backend.core.openai_retry.time.monotonic", _monotonic)
    monkeypatch.setattr("backend.core.openai_retry.time.sleep", _sleep)
    monkeypatch.setattr("backend.core.openai_retry.settings.openai_user_retry_budget_seconds", 0.5)

    with pytest.raises(InternalServerError):
        call_openai_with_retry(
            "chat_generate",
            lambda: (_ for _ in ()).throw(
                InternalServerError("boom", response=_response(500), body=None)
            ),
        )

    assert clock["value"] <= 0.5


def test_jitter_is_within_bounds() -> None:
    classified = ClassifiedError(
        kind=OpenAIFailureKind.TRANSIENT,
        retry_after_seconds=None,
        status_code=500,
        message="boom",
    )

    delay = _delay_for_user(classified=classified, attempt=2, budget_seconds=1.5)

    assert 0.6 <= delay <= 0.78


def test_logs_retry_event_with_operation_label(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = {"count": 0}
    monkeypatch.setattr("backend.core.openai_retry.time.sleep", lambda _: None)
    caplog.set_level(logging.INFO, logger="backend.core.openai_retry")

    def _fn() -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            raise APITimeoutError(request=_request())
        return "ok"

    assert call_openai_with_retry("chat_generate", _fn) == "ok"

    assert any(
        record.msg == "openai_user_retry" and record.operation == "chat_generate"
        for record in caplog.records
    )
