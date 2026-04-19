"""Tests for the PostHog metrics facade."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.observability.metrics import MetricsService, capture_event, get_metrics


@pytest.fixture(autouse=True)
def _reset_metrics_singleton():
    svc = get_metrics()
    svc.reset()
    yield
    svc.reset()


def test_init_noop_in_test_environment(monkeypatch):
    monkeypatch.setattr("backend.observability.metrics.settings.environment", "test")
    monkeypatch.setattr(
        "backend.observability.metrics.settings.posthog_api_key", "phc_real"
    )
    svc = MetricsService()
    svc.init()
    assert svc.enabled is False


def test_init_noop_without_api_key(monkeypatch):
    monkeypatch.setattr(
        "backend.observability.metrics.settings.environment", "development"
    )
    monkeypatch.setattr("backend.observability.metrics.settings.posthog_api_key", None)
    svc = MetricsService()
    svc.init()
    assert svc.enabled is False


def test_capture_is_safe_when_disabled():
    svc = MetricsService()
    svc.capture(
        "language.resolved",
        distinct_id="bot_abc",
        tenant_id="tnt_abc",
        properties={"detected": "en"},
    )
    assert svc.enabled is False


def test_capture_swallows_client_errors(monkeypatch):
    monkeypatch.setattr(
        "backend.observability.metrics.settings.environment", "development"
    )
    monkeypatch.setattr(
        "backend.observability.metrics.settings.posthog_api_key", "phc_real"
    )

    class _ExplodingPosthog:
        def __init__(self, *args, **kwargs):
            pass

        def capture(self, *args, **kwargs):
            raise RuntimeError("boom")

        def flush(self):
            pass

        def shutdown(self):
            pass

    with patch.dict("sys.modules", {"posthog": type("M", (), {"Posthog": _ExplodingPosthog})}):
        svc = MetricsService()
        svc.init()
        assert svc.enabled is True
        svc.capture("test.event", distinct_id="x")  # must not raise
        svc.shutdown()


def test_module_capture_event_noop_when_disabled():
    capture_event(
        "language.resolved",
        distinct_id="bot_abc",
        tenant_id="tnt_abc",
        properties={"detected": "en"},
    )


def test_capture_strips_reserved_keys_from_properties(monkeypatch):
    monkeypatch.setattr(
        "backend.observability.metrics.settings.environment", "development"
    )
    monkeypatch.setattr("backend.observability.metrics.settings.git_sha", "deadbeef")
    monkeypatch.setattr(
        "backend.observability.metrics.settings.posthog_api_key", "phc_real"
    )

    captured: dict = {}

    class _RecordingPosthog:
        def __init__(self, *args, **kwargs):
            pass

        def capture(self, **kwargs):
            captured.update(kwargs)

        def flush(self):
            pass

        def shutdown(self):
            pass

    with patch.dict(
        "sys.modules",
        {"posthog": type("M", (), {"Posthog": _RecordingPosthog})},
    ):
        svc = MetricsService()
        svc.init()
        svc.capture(
            "language.resolved",
            distinct_id="bot_abc",
            tenant_id="tnt_abc",
            bot_id="bot_abc",
            properties={
                "detected": "en",
                # Reserved keys should be ignored when supplied via properties.
                "tenant_id": "OVERRIDE_ATTEMPT",
                "environment": "OVERRIDE_ATTEMPT",
            },
        )

    props = captured["properties"]
    assert props["tenant_id"] == "tnt_abc"
    assert props["bot_id"] == "bot_abc"
    assert props["environment"] == "development"
    assert props["release"] == "deadbeef"
    assert props["detected"] == "en"
    assert captured["distinct_id"] == "bot_abc"
    assert captured["event"] == "language.resolved"
