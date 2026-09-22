"""Verify quick_answer.* PostHog events fire from run_chat_pipeline."""

from __future__ import annotations

import pytest

from backend.chat.events import _emit_quick_answer_lookup_event


@pytest.fixture
def captured_events(monkeypatch):
    events: list[dict] = []

    def fake_capture(event, **kwargs):
        events.append({"event": event, **kwargs})

    monkeypatch.setattr("backend.chat.service.capture_event", fake_capture)
    return events


@pytest.mark.parametrize(
    (
        "selected_keys",
        "matched_count",
        "bot_public_id",
        "expected_distinct_id",
        "expected_found",
    ),
    [
        pytest.param(
            ["pricing_url", "trial_info"],
            2,
            "bot_test",
            "bot_test",
            True,
            id="hit_prefers_bot_distinct_id",
        ),
        pytest.param(
            ["pricing_url"],
            0,
            None,
            "tnt_test",
            False,
            id="miss_falls_back_to_tenant_distinct_id",
        ),
    ],
)
def test_emits_quick_answer_lookup_event(
    captured_events,
    selected_keys,
    matched_count,
    bot_public_id,
    expected_distinct_id,
    expected_found,
):
    _emit_quick_answer_lookup_event(
        selected_keys=selected_keys,
        matched_count=matched_count,
        text_length=42,
        tenant_public_id="tnt_test",
        bot_public_id=bot_public_id,
        chat_id="chat_test",
    )

    assert len(captured_events) == 1
    e = captured_events[0]
    assert e["event"] == "quick_answer.lookup"
    assert e["distinct_id"] == expected_distinct_id
    assert e["tenant_id"] == "tnt_test"
    assert e["bot_id"] == bot_public_id
    props = e["properties"]
    assert props["selected_keys"] == ",".join(selected_keys)
    assert props["selected_count"] == len(selected_keys)
    assert props["matched_count"] == matched_count
    assert props["found"] is expected_found
    assert props["chat_id"] == "chat_test"


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
