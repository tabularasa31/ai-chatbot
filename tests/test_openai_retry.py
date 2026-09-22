from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError, AuthenticationError, InternalServerError, RateLimitError

from backend.core.openai_retry import (
    _delay_for_user,
    async_call_openai_with_retry,
    call_openai_with_retry,
    provider_response_stamps,
)
from backend.core.openai_errors import ClassifiedError, OpenAIFailureKind


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _response(status_code: int, *, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status_code, request=_request(), headers=headers)


@pytest.mark.parametrize(
    "make_error",
    [
        lambda: InternalServerError("boom", response=_response(500), body=None),
        lambda: RateLimitError(
            "rate limited", response=_response(429, headers={"retry-after": "1"}), body=None
        ),
    ],
    ids=["transient-500", "rate-limit-429-short-retry-after"],
)
def test_retry_recovers_after_one_transient_failure(
    monkeypatch: pytest.MonkeyPatch, make_error
) -> None:
    calls = {"count": 0}
    sleeps: list[float] = []
    monkeypatch.setattr("backend.core.openai_retry.time.sleep", sleeps.append)

    def _fn() -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            raise make_error()
        return "ok"

    assert call_openai_with_retry("chat_generate", _fn) == "ok"
    assert calls["count"] == 2
    assert len(sleeps) == 1


@pytest.mark.parametrize(
    "error,expected_exc",
    [
        (lambda: APITimeoutError(request=_request()), APITimeoutError),
        (lambda: AuthenticationError("auth", response=_response(401), body=None), AuthenticationError),
        (
            lambda: RateLimitError(
                "rate limited", response=_response(429, headers={"retry-after": "10"}), body=None
            ),
            RateLimitError,
        ),
    ],
    ids=["timeout-not-retried", "permanent-401-not-retried", "rate-limit-retry-after-exceeds-budget"],
)
def test_no_retry_reraises_immediately(
    monkeypatch: pytest.MonkeyPatch, error, expected_exc
) -> None:
    monkeypatch.setattr("backend.core.openai_retry.time.sleep", lambda _: None)
    calls = {"count": 0}

    def _fn() -> str:
        calls["count"] += 1
        raise error()

    with pytest.raises(expected_exc):
        call_openai_with_retry("chat_generate", _fn)

    assert calls["count"] == 1


def test_retry_exhausts_then_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.core.openai_retry.time.sleep", lambda _: None)

    with pytest.raises(InternalServerError):
        call_openai_with_retry(
            "chat_generate",
            lambda: (_ for _ in ()).throw(
                InternalServerError("boom", response=_response(500), body=None)
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


class _RecordingObservation:
    """Test double mimicking the SpanHandle.update_metadata contract."""

    def __init__(self, *, raise_on_update: bool = False) -> None:
        self.updates: list[dict[str, object]] = []
        self._raise = raise_on_update

    def update_metadata(self, **kvs: object) -> None:
        if self._raise:
            raise RuntimeError("observation_broken")
        self.updates.append(dict(kvs))


def test_observation_stamped_on_first_attempt_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("backend.core.openai_retry.time.sleep", lambda _: None)
    obs = _RecordingObservation()

    result = call_openai_with_retry(
        "chat_generate",
        lambda: "ok",
        langfuse_observation=obs,
    )

    assert result == "ok"
    assert obs.updates == [{"attempt_count": 1, "was_retried": False}]


def test_observation_stamped_after_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.core.openai_retry.time.sleep", lambda _: None)
    obs = _RecordingObservation()
    calls = {"count": 0}

    def _fn() -> str:
        calls["count"] += 1
        if calls["count"] < 3:
            raise InternalServerError("boom", response=_response(500), body=None)
        return "ok"

    result = call_openai_with_retry("chat_generate", _fn, langfuse_observation=obs)

    assert result == "ok"
    assert calls["count"] == 3
    assert obs.updates == [{"attempt_count": 3, "was_retried": True}]


def test_observation_stamped_on_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.core.openai_retry.time.sleep", lambda _: None)
    obs = _RecordingObservation()

    with pytest.raises(InternalServerError):
        call_openai_with_retry(
            "chat_generate",
            lambda: (_ for _ in ()).throw(
                InternalServerError("boom", response=_response(500), body=None)
            ),
            langfuse_observation=obs,
        )

    exhausted = [u for u in obs.updates if u.get("retry_exhausted")]
    assert exhausted, f"no exhausted stamp recorded; got {obs.updates}"
    final = exhausted[-1]
    assert final["was_retried"] is True
    assert "retry_failure_kind" in final


@pytest.mark.parametrize(
    "error,expected_exc,failure_kind",
    [
        (lambda: APITimeoutError(request=_request()), APITimeoutError, "timeout"),
        (
            lambda: AuthenticationError("auth", response=_response(401), body=None),
            AuthenticationError,
            "permanent",
        ),
    ],
    ids=["timeout-no-retry", "permanent-error"],
)
def test_observation_stamped_on_immediate_failure(
    monkeypatch: pytest.MonkeyPatch, error, expected_exc, failure_kind
) -> None:
    monkeypatch.setattr("backend.core.openai_retry.time.sleep", lambda _: None)
    obs = _RecordingObservation()

    with pytest.raises(expected_exc):
        call_openai_with_retry(
            "chat_generate",
            lambda: (_ for _ in ()).throw(error()),
            langfuse_observation=obs,
        )

    assert obs.updates == [
        {
            "attempt_count": 1,
            "was_retried": False,
            "retry_exhausted": True,
            "retry_failure_kind": failure_kind,
        }
    ]


@pytest.mark.parametrize(
    "make_observation",
    [
        lambda: None,
        lambda: _RecordingObservation(raise_on_update=True),
        lambda: SimpleNamespace(),
    ],
    ids=["none-observation", "update_metadata-raises", "duck-typed-without-method"],
)
def test_retry_result_survives_observation_edge_cases(
    monkeypatch: pytest.MonkeyPatch, make_observation
) -> None:
    """Result must be returned regardless of a missing/broken observation handle."""
    monkeypatch.setattr("backend.core.openai_retry.time.sleep", lambda _: None)

    assert (
        call_openai_with_retry(
            "chat_generate", lambda: "ok", langfuse_observation=make_observation()
        )
        == "ok"
    )


def test_async_observation_stamped_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Async wrapper stamps the observation just like the sync wrapper."""

    async def _runner() -> None:
        obs = _RecordingObservation()

        async def _fn() -> str:
            return "ok"

        async def _no_sleep(_: float) -> None:
            return None

        monkeypatch.setattr("backend.core.openai_retry.asyncio.sleep", _no_sleep)

        result = await async_call_openai_with_retry(
            "chat_generate", _fn, langfuse_observation=obs
        )

        assert result == "ok"
        assert obs.updates == [{"attempt_count": 1, "was_retried": False}]

    asyncio.run(_runner())


@pytest.mark.parametrize(
    "response,expected_extra",
    [
        (
            SimpleNamespace(id="chatcmpl-abc123", system_fingerprint="fp_44709d6f"),
            {"provider_request_id": "chatcmpl-abc123", "system_fingerprint": "fp_44709d6f"},
        ),
        (SimpleNamespace(id=None, system_fingerprint=None), {}),
    ],
    ids=["present-provider-identifiers", "missing-provider-identifiers"],
)
def test_observation_stamped_with_provider_identifiers(
    monkeypatch: pytest.MonkeyPatch, response, expected_extra
) -> None:
    """A response without string identifiers (embeddings, streams, mocks)
    leaves the stamp as before instead of writing ``None`` onto every span."""
    monkeypatch.setattr("backend.core.openai_retry.time.sleep", lambda _: None)
    obs = _RecordingObservation()

    result = call_openai_with_retry(
        "chat_generate", lambda: response, langfuse_observation=obs
    )

    assert result is response
    assert obs.updates == [{"attempt_count": 1, "was_retried": False, **expected_extra}]


def test_async_observation_stamped_with_provider_identifiers(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _runner() -> None:
        obs = _RecordingObservation()
        response = SimpleNamespace(id="chatcmpl-async", system_fingerprint="fp_async")

        async def _fn() -> SimpleNamespace:
            return response

        async def _no_sleep(_: float) -> None:
            return None

        monkeypatch.setattr("backend.core.openai_retry.asyncio.sleep", _no_sleep)

        result = await async_call_openai_with_retry(
            "chat_generate", _fn, langfuse_observation=obs
        )

        assert result is response
        assert obs.updates == [
            {
                "attempt_count": 1,
                "was_retried": False,
                "provider_request_id": "chatcmpl-async",
                "system_fingerprint": "fp_async",
            }
        ]

    asyncio.run(_runner())


def test_provider_response_stamps_always_has_both_keys() -> None:
    assert provider_response_stamps(None) == {
        "provider_request_id": None,
        "system_fingerprint": None,
    }
    assert provider_response_stamps(SimpleNamespace(id="chatcmpl-x")) == {
        "provider_request_id": "chatcmpl-x",
        "system_fingerprint": None,
    }


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
            raise InternalServerError("boom", response=_response(500), body=None)
        return "ok"

    assert call_openai_with_retry("chat_generate", _fn) == "ok"

    assert any(
        record.msg == "openai_user_retry" and record.operation == "chat_generate"
        for record in caplog.records
    )
