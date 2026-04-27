"""Verify quick_answer.* PostHog events fire from run_chat_pipeline."""

from __future__ import annotations

import pytest

from backend.chat.service import _emit_quick_answer_lookup_event


@pytest.fixture
def captured_events(monkeypatch):
    events: list[dict] = []

    def fake_capture(event, **kwargs):
        events.append({"event": event, **kwargs})

    monkeypatch.setattr("backend.chat.service.capture_event", fake_capture)
    return events


def test_emits_quick_answer_lookup_hit(captured_events):
    _emit_quick_answer_lookup_event(
        selected_keys=["pricing_url", "trial_info"],
        matched_count=2,
        text_length=42,
        tenant_public_id="tnt_test",
        bot_public_id="bot_test",
        chat_id="chat_test",
    )

    assert len(captured_events) == 1
    e = captured_events[0]
    assert e["event"] == "quick_answer.lookup"
    assert e["distinct_id"] == "bot_test"
    assert e["tenant_id"] == "tnt_test"
    assert e["bot_id"] == "bot_test"
    props = e["properties"]
    assert props["selected_keys"] == "pricing_url,trial_info"
    assert props["selected_count"] == 2
    assert props["matched_count"] == 2
    assert props["found"] is True
    assert props["text_length"] == 42
    assert props["chat_id"] == "chat_test"


def test_emits_quick_answer_lookup_miss(captured_events):
    _emit_quick_answer_lookup_event(
        selected_keys=["pricing_url"],
        matched_count=0,
        text_length=10,
        tenant_public_id="tnt_test",
        bot_public_id=None,
        chat_id="chat_test",
    )

    assert len(captured_events) == 1
    e = captured_events[0]
    assert e["distinct_id"] == "tnt_test"
    assert e["properties"]["matched_count"] == 0
    assert e["properties"]["found"] is False


def test_skips_emit_when_no_identifiers(captured_events):
    _emit_quick_answer_lookup_event(
        selected_keys=["pricing_url"],
        matched_count=1,
        text_length=10,
        tenant_public_id=None,
        bot_public_id=None,
        chat_id=None,
    )

    assert captured_events == []


def test_telemetry_failure_does_not_propagate(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("posthog down")

    monkeypatch.setattr("backend.chat.service.capture_event", boom)

    # Must not raise.
    _emit_quick_answer_lookup_event(
        selected_keys=["pricing_url"],
        matched_count=1,
        text_length=10,
        tenant_public_id="tnt_test",
        bot_public_id=None,
        chat_id=None,
    )
