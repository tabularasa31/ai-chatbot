"""Verify openai_retry.* PostHog events fire from call_openai_with_retry."""

from __future__ import annotations

import httpx
import pytest
from openai import APITimeoutError, AuthenticationError, InternalServerError, RateLimitError

from backend.core.openai_retry import call_openai_with_retry


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _response(status_code: int, *, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status_code, request=_request(), headers=headers)


@pytest.fixture
def captured_events(monkeypatch):
    events: list[dict] = []

    def fake_capture(event, **kwargs):
        events.append({"event": event, **kwargs})

    monkeypatch.setattr("backend.core.openai_retry.capture_event", fake_capture)
    monkeypatch.setattr("backend.core.openai_retry.time.sleep", lambda _: None)
    return events


def _named(events: list[dict], name: str) -> list[dict]:
    return [e for e in events if e["event"] == name]


def test_emits_attempt_on_every_call(captured_events):
    """openai_retry.attempt fires once per fn() call, including successful ones."""
    calls = {"count": 0}

    def _fn() -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            raise InternalServerError("boom", response=_response(500), body=None)
        return "ok"

    result = call_openai_with_retry("chat_generate", _fn, tenant_id="tnt_test")

    assert result == "ok"
    attempts = _named(captured_events, "openai_retry.attempt")
    # Two fn() calls: attempt=1 (failed), attempt=2 (succeeded)
    assert len(attempts) == 2
    assert attempts[0]["properties"]["attempt"] == 1
    assert attempts[1]["properties"]["attempt"] == 2
    assert attempts[0]["distinct_id"] == "tnt_test"
    assert attempts[0]["tenant_id"] == "tnt_test"
    assert attempts[0]["properties"]["operation"] == "chat_generate"
    assert isinstance(attempts[0]["properties"]["elapsed_ms"], int)


def test_emits_retry_scheduled_when_retrying(captured_events):
    """openai_retry.retry_scheduled fires only when a sleep+retry is scheduled."""
    calls = {"count": 0}

    def _fn() -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            raise InternalServerError("boom", response=_response(500), body=None)
        return "ok"

    call_openai_with_retry("chat_generate", _fn, tenant_id="tnt_test")

    scheduled = _named(captured_events, "openai_retry.retry_scheduled")
    assert len(scheduled) == 1
    props = scheduled[0]["properties"]
    assert props["operation"] == "chat_generate"
    assert props["attempt"] == 1
    assert props["failure_kind"] == "transient"
    assert isinstance(props["delay_ms"], int)
    assert props["delay_ms"] >= 0


def test_attempt_count_never_less_than_exhausted(captured_events):
    """Core invariant: attempt >= exhausted, even when first call immediately exhausts."""
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
            tenant_id="tnt_test",
        )

    attempts = _named(captured_events, "openai_retry.attempt")
    exhausted = _named(captured_events, "openai_retry.exhausted")
    assert len(exhausted) == 1
    assert len(attempts) >= len(exhausted), (
        f"attempt ({len(attempts)}) must be >= exhausted ({len(exhausted)})"
    )


def test_emits_exhausted_when_max_attempts_reached(captured_events):
    with pytest.raises(InternalServerError):
        call_openai_with_retry(
            "chat_validate",
            lambda: (_ for _ in ()).throw(
                InternalServerError("boom", response=_response(500), body=None)
            ),
            tenant_id="tnt_test",
            bot_id="bot_xyz",
        )

    exhausted = _named(captured_events, "openai_retry.exhausted")
    assert len(exhausted) == 1
    e = exhausted[0]
    assert e["distinct_id"] == "bot_xyz"
    assert e["bot_id"] == "bot_xyz"
    props = e["properties"]
    assert props["operation"] == "chat_validate"
    assert props["failure_kind"] == "transient"
    assert props["reason"] in {"max_attempts", "budget_exhausted"}
    assert isinstance(props["elapsed_ms"], int)

    # attempt events must have fired at least as many times as exhausted
    attempts = _named(captured_events, "openai_retry.attempt")
    assert len(attempts) >= len(exhausted)


def test_emits_exhausted_when_rate_limit_over_budget(captured_events):
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
            tenant_id="tnt_test",
        )

    exhausted = _named(captured_events, "openai_retry.exhausted")
    assert len(exhausted) == 1
    assert exhausted[0]["properties"]["reason"] == "rate_limit_over_budget"


def test_no_emit_on_permanent_error(captured_events):
    with pytest.raises(AuthenticationError):
        call_openai_with_retry(
            "chat_generate",
            lambda: (_ for _ in ()).throw(
                AuthenticationError("auth", response=_response(401), body=None)
            ),
            tenant_id="tnt_test",
        )

    assert _named(captured_events, "openai_retry.exhausted") == []
    assert _named(captured_events, "openai_retry.retry_scheduled") == []
    # attempt IS emitted (we did call fn() once before it raised PERMANENT)
    attempts = _named(captured_events, "openai_retry.attempt")
    assert len(attempts) == 1


def test_distinct_id_falls_back_to_system_when_no_identifiers(captured_events):
    with pytest.raises(InternalServerError):
        call_openai_with_retry(
            "chat_generate",
            lambda: (_ for _ in ()).throw(
                InternalServerError("boom", response=_response(500), body=None)
            ),
        )

    exhausted = _named(captured_events, "openai_retry.exhausted")
    assert exhausted[0]["distinct_id"] == "system"
    assert exhausted[0]["tenant_id"] is None
    assert exhausted[0]["bot_id"] is None


def test_attempt_event_includes_call_type_default(captured_events):
    """openai_retry.attempt includes call_type='chat_completion' by default."""
    call_openai_with_retry("chat_generate", lambda: "ok", tenant_id="tnt_test")

    attempts = _named(captured_events, "openai_retry.attempt")
    assert len(attempts) == 1
    assert attempts[0]["properties"]["call_type"] == "chat_completion"


def test_attempt_event_includes_call_type_embedding(captured_events):
    """openai_retry.attempt includes call_type='embedding' when explicitly passed."""
    call_openai_with_retry(
        "search_embed_query",
        lambda: "ok",
        tenant_id="tnt_test",
        call_type="embedding",
    )

    attempts = _named(captured_events, "openai_retry.attempt")
    assert len(attempts) == 1
    assert attempts[0]["properties"]["call_type"] == "embedding"


def test_exhausted_event_includes_call_type(captured_events):
    """openai_retry.exhausted includes call_type property."""
    with pytest.raises(InternalServerError):
        call_openai_with_retry(
            "search_embed_query",
            lambda: (_ for _ in ()).throw(
                InternalServerError("boom", response=_response(500), body=None)
            ),
            tenant_id="tnt_test",
            call_type="embedding",
        )

    exhausted = _named(captured_events, "openai_retry.exhausted")
    assert len(exhausted) == 1
    assert exhausted[0]["properties"]["call_type"] == "embedding"


def test_capture_event_failure_does_not_break_retry(monkeypatch):
    monkeypatch.setattr("backend.core.openai_retry.time.sleep", lambda _: None)

    def boom(*args, **kwargs):
        raise RuntimeError("posthog down")

    monkeypatch.setattr("backend.core.openai_retry.capture_event", boom)

    calls = {"count": 0}

    def _fn() -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            raise InternalServerError("boom", response=_response(500), body=None)
        return "ok"

    # Telemetry failure must not break the retry loop or the result.
    assert call_openai_with_retry("chat_generate", _fn, tenant_id="tnt") == "ok"


def test_no_emit_retry_scheduled_on_timeout(captured_events):
    """APITimeoutError emits exhausted but NOT retry_scheduled."""
    with pytest.raises(APITimeoutError):
        call_openai_with_retry(
            "chat_generate",
            lambda: (_ for _ in ()).throw(APITimeoutError(request=_request())),
            tenant_id="tnt_test",
        )

    assert _named(captured_events, "openai_retry.retry_scheduled") == []
    exhausted = _named(captured_events, "openai_retry.exhausted")
    assert len(exhausted) == 1
    assert exhausted[0]["properties"]["reason"] == "timeout_no_retry"
    assert exhausted[0]["properties"]["failure_kind"] == "timeout"


def test_chat_failed_emitted_on_exhausted_chat_completion(captured_events):
    """chat.failed fires when emit_chat_failed=True and retries are exhausted."""
    with pytest.raises(InternalServerError):
        call_openai_with_retry(
            "chat_generate",
            lambda: (_ for _ in ()).throw(
                InternalServerError("boom", response=_response(500), body=None)
            ),
            tenant_id="tnt_test",
            bot_id="bot_xyz",
            emit_chat_failed=True,
        )

    chat_failed = _named(captured_events, "chat.failed")
    assert len(chat_failed) == 1
    e = chat_failed[0]
    assert e["tenant_id"] == "tnt_test"
    assert e["bot_id"] == "bot_xyz"
    props = e["properties"]
    assert props["operation"] == "chat_generate"
    assert props["failure_kind"] == "transient"
    assert props["error_type"] == "InternalServerError"
    assert props["call_type"] == "chat_completion"
    assert props["reason"] in {"max_attempts", "budget_exhausted", "delay_over_remaining"}
    assert isinstance(props["attempt_count"], int)
    assert isinstance(props["elapsed_ms"], int)


def test_chat_failed_not_emitted_without_flag(captured_events):
    """chat.failed must not fire when emit_chat_failed is not set (default False)."""
    with pytest.raises(InternalServerError):
        call_openai_with_retry(
            "search_embed_query",
            lambda: (_ for _ in ()).throw(
                InternalServerError("boom", response=_response(500), body=None)
            ),
            tenant_id="tnt_test",
        )

    assert _named(captured_events, "chat.failed") == []


def test_chat_failed_emitted_on_timeout(captured_events):
    """chat.failed fires on first-attempt APITimeoutError when emit_chat_failed=True."""
    with pytest.raises(APITimeoutError):
        call_openai_with_retry(
            "chat_generate",
            lambda: (_ for _ in ()).throw(APITimeoutError(request=_request())),
            tenant_id="tnt_test",
            emit_chat_failed=True,
        )

    chat_failed = _named(captured_events, "chat.failed")
    assert len(chat_failed) == 1
    props = chat_failed[0]["properties"]
    assert props["failure_kind"] == "timeout"
    assert props["error_type"] == "APITimeoutError"
    assert props["reason"] == "timeout_no_retry"
    assert isinstance(props["attempt_count"], int)
    assert isinstance(props["elapsed_ms"], int)
