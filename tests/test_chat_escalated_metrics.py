"""Verify chat_escalated PostHog event fires with correct properties."""

from __future__ import annotations

import pytest

from backend.chat.events import _emit_chat_escalated_event


@pytest.fixture
def captured_events(monkeypatch):
    events: list[dict] = []

    def fake_capture(event, **kwargs):
        events.append({"event": event, **kwargs})

    monkeypatch.setattr("backend.chat.events.capture_event", fake_capture)
    return events


def test_emits_chat_escalated_with_reason(captured_events):
    _emit_chat_escalated_event(
        tenant_public_id="tnt_test",
        bot_public_id="bot_test",
        chat_id="chat_test",
        escalation_reason="low_confidence",
        escalation_trigger="auto",
    )

    assert len(captured_events) == 1
    e = captured_events[0]
    assert e["event"] == "chat_escalated"
    assert e["distinct_id"] == "chat_test"
    assert e["tenant_id"] == "tnt_test"
    assert e["bot_id"] == "bot_test"
    props = e["properties"]
    assert props["reason"] == "low_confidence"
    assert props["escalation_reason"] == "low_confidence"
    assert props["escalation_trigger"] == "auto"
    assert props["chat_id"] == "chat_test"


def test_skips_emit_when_no_identifiers(captured_events):
    _emit_chat_escalated_event(
        tenant_public_id=None,
        bot_public_id=None,
        chat_id="chat_test",
        escalation_reason="low_confidence",
    )

    assert captured_events == []


def test_telemetry_failure_does_not_propagate(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("posthog down")

    monkeypatch.setattr("backend.chat.events.capture_event", boom)

    # Must not raise.
    _emit_chat_escalated_event(
        tenant_public_id="tnt_test",
        bot_public_id=None,
        chat_id="chat_test",
        escalation_reason="explicit_human_request",
    )
